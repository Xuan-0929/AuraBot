/**
 * Verifies the Pet's minimal voice-session contract at browser boundaries. The suite covers profile-bound
 * token acquisition, one shared AudioContext, raw microphone/TTS PCM, server/local playback gates, physical
 * cursor ACK, and one teardown path without relying on the removed diagnostic protocol or real hardware.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { MainAgentRealtimeClient } from "@renderer/main-agent-realtime/client";

class FakePort {
  onmessage: ((event: MessageEvent) => void) | null = null;
  readonly sent: unknown[] = [];

  postMessage(value: unknown): void {
    this.sent.push(value);
  }
}

class FakeAudioWorkletNode {
  static readonly instances: FakeAudioWorkletNode[] = [];
  readonly port = new FakePort();

  constructor(_context: unknown, public readonly processorName: string) {
    FakeAudioWorkletNode.instances.push(this);
  }

  connect(): void {}
}

class FakeAudioContext {
  static readonly instances: FakeAudioContext[] = [];
  readonly audioWorklet = { addModule: vi.fn(async () => undefined) };
  readonly destination = {};
  readonly close = vi.fn(async () => undefined);
  readonly resume = vi.fn(async () => undefined);

  constructor() {
    FakeAudioContext.instances.push(this);
  }

  createMediaStreamSource(): { connect: () => void } {
    return { connect: () => undefined };
  }
}

class FakeWebSocket {
  static readonly OPEN = 1;
  static readonly instances: FakeWebSocket[] = [];
  readyState = 0;
  binaryType = "";
  bufferedAmount = 0;
  readonly sent: Array<string | ArrayBuffer> = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(public readonly url: URL, public readonly protocols: string[]) {
    FakeWebSocket.instances.push(this);
  }

  send(value: string | ArrayBuffer): void {
    this.sent.push(value);
  }

  close(): void {
    this.readyState = 3;
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  message(data: string | ArrayBuffer): void {
    this.onmessage?.({ data } as MessageEvent);
  }
}

function sentControls(socket: FakeWebSocket): Array<Record<string, unknown>> {
  return socket.sent
    .filter((value): value is string => typeof value === "string")
    .map((value) => JSON.parse(value) as Record<string, unknown>);
}

describe("formal Pet Main Agent realtime client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    FakeWebSocket.instances.length = 0;
    FakeAudioWorkletNode.instances.length = 0;
    FakeAudioContext.instances.length = 0;
  });

  it("binds profile and soul, streams raw PCM, and ACKs only after physical playback", async () => {
    const stopTrack = vi.fn();
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ token: "one-shot-token" }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("navigator", {
      mediaDevices: {
        getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: stopTrack }] })),
      },
    });
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("AudioWorkletNode", FakeAudioWorkletNode);
    const states: string[] = [];
    const clauses: Array<string | null> = [];
    const client = new MainAgentRealtimeClient({
      onState: (state) => states.push(state),
      onClause: (text) => clauses.push(text),
    });

    await client.start({ profileId: "profile_default", soul: "名字：Aura\n语气：自然" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:6401/api/v1/main-agent/realtime-token",
      expect.objectContaining({
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ profileId: "profile_default", soul: "名字：Aura\n语气：自然" }),
      }),
    );
    const socket = FakeWebSocket.instances[0];
    expect(socket.protocols).toEqual([
      "aurabot.main-agent.v1",
      "aurabot.capability.one-shot-token",
    ]);
    socket.open();
    expect(sentControls(socket)).toEqual([]);
    socket.message(JSON.stringify({ type: "session.ready" }));
    await vi.waitFor(() => {
      expect(FakeAudioWorkletNode.instances.some(
        (node) => node.processorName === "aurabot-mic-capture",
      )).toBe(true);
    });
    expect(states).toContain("active");
    expect(FakeAudioContext.instances).toHaveLength(1);

    const mic = FakeAudioWorkletNode.instances.find(
      (node) => node.processorName === "aurabot-mic-capture",
    );
    mic?.port.onmessage?.({
      data: { type: "frame", samples: new Int16Array(320).fill(7) },
    } as MessageEvent);
    const micFrame = socket.sent.find((value): value is ArrayBuffer => value instanceof ArrayBuffer);
    expect(micFrame?.byteLength).toBe(640);

    socket.message(JSON.stringify({
      type: "clause.offer",
      clause_id: "clause-9",
      text: "真实播放后再确认",
    }));
    expect(clauses.at(-1)).toBe("真实播放后再确认");
    socket.message(new Int16Array(480).fill(100).buffer);
    socket.message(JSON.stringify({ type: "clause.audio_end", clause_id: "clause-9" }));
    await vi.waitFor(() => {
      expect(FakeAudioWorkletNode.instances.some(
        (node) => node.processorName === "aurabot-tts-playback",
      )).toBe(true);
    });
    expect(sentControls(socket).some((event) => event.type === "renderer.ack")).toBe(false);

    const playback = FakeAudioWorkletNode.instances.find(
      (node) => node.processorName === "aurabot-tts-playback",
    );
    playback?.port.onmessage?.({
      data: { type: "clause-complete", clauseId: "clause-9" },
    } as MessageEvent);
    const ack = sentControls(socket).find((event) => event.type === "renderer.ack");
    expect(ack).toMatchObject({
      clause_id: "clause-9",
      complete: true,
    });
    expect(clauses.at(-1)).toBeNull();

    await client.stop();
    expect(stopTrack).toHaveBeenCalledOnce();
    expect(states.at(-1)).toBe("off");
  });

  it("keeps local and server pause independent and sends at most one partial ACK on teardown", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ token: "token" }) })));
    vi.stubGlobal("navigator", {
      mediaDevices: { getUserMedia: vi.fn(async () => ({ getTracks: () => [] })) },
    });
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("AudioWorkletNode", FakeAudioWorkletNode);
    const client = new MainAgentRealtimeClient();

    await client.start({ profileId: "profile_default", soul: "Aura" });
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.message(JSON.stringify({ type: "session.ready" }));
    await vi.waitFor(() => expect(FakeAudioWorkletNode.instances).toHaveLength(2));
    socket.message(JSON.stringify({ type: "clause.offer", clause_id: "c1", text: "hello" }));
    const mic = FakeAudioWorkletNode.instances.find((node) => node.processorName === "aurabot-mic-capture");
    const playback = FakeAudioWorkletNode.instances.find((node) => node.processorName === "aurabot-tts-playback");
    const resumeCount = () => playback?.port.sent.filter(
      (value) => (value as { type?: string }).type === "resume",
    ).length ?? 0;
    const initialResumes = resumeCount();
    mic?.port.onmessage?.({ data: { type: "candidate" } } as MessageEvent);
    socket.message(JSON.stringify({ type: "playback.pause" }));
    mic?.port.onmessage?.({ data: { type: "released" } } as MessageEvent);
    expect(resumeCount()).toBe(initialResumes);
    socket.message(JSON.stringify({ type: "playback.resume" }));
    expect(resumeCount()).toBe(initialResumes + 1);

    await client.stop();
    const partial = sentControls(socket).filter(
      (event) => event.type === "renderer.ack" && event.complete === false,
    );
    expect(partial).toHaveLength(1);
  });

  it("fully tears down a session when microphone permission is denied", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ token: "token" }) })));
    vi.stubGlobal("navigator", {
      mediaDevices: { getUserMedia: vi.fn(async () => { throw new Error("permission denied"); }) },
    });
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("AudioWorkletNode", FakeAudioWorkletNode);
    const states: string[] = [];
    const client = new MainAgentRealtimeClient({ onState: (state) => states.push(state) });

    await client.start({ profileId: "profile_default", soul: "Aura" });
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.message(JSON.stringify({ type: "session.ready" }));
    await vi.waitFor(() => expect(client.currentState).toBe("error"));

    expect(socket.readyState).toBe(3);
    expect(FakeAudioContext.instances).toHaveLength(0);
    expect(states.at(-1)).toBe("error");
  });
});
