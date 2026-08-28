/**
 * Exercises the formal Pet Main Agent client as one browser-side lifecycle with deterministic media and
 * transport doubles. It proves token acquisition, voice lease opening, mic admission, clause playback,
 * physical-cursor complete ACK, bubble cleanup, and teardown without requiring microphone hardware.
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
  readonly audioWorklet = { addModule: vi.fn(async () => undefined) };
  readonly destination = {};
  readonly close = vi.fn(async () => undefined);
  readonly resume = vi.fn(async () => undefined);

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

function finalTtsFrame(): ArrayBuffer {
  const encoded = new ArrayBuffer(24 + 960);
  const bytes = new Uint8Array(encoded);
  bytes.set(new TextEncoder().encode("AUR1"));
  const view = new DataView(encoded);
  view.setUint8(4, 2);
  view.setUint8(5, 2);
  return encoded;
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
  });

  it("automatically ACKs only after the playback cursor completes the offered clause", async () => {
    let commandId = 0;
    const stopTrack = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => ({ token: "one-shot-token" }),
    })));
    vi.stubGlobal("crypto", { randomUUID: () => `00000000-0000-4000-8000-${String(++commandId).padStart(12, "0")}` });
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

    await client.start();
    const socket = FakeWebSocket.instances[0];
    expect(socket.protocols).toEqual([
      "aurabot.main-agent.v1",
      "aurabot.capability.one-shot-token",
    ]);
    socket.open();
    expect(sentControls(socket)[0]?.type).toBe("session.open");

    socket.message(JSON.stringify({
      type: "session.opened",
      renderer_epoch: 9,
      mode: "voice",
    }));
    await vi.waitFor(() => {
      expect(sentControls(socket).some((event) => event.type === "mic.start")).toBe(true);
    });
    expect(states).toContain("active");

    socket.message(JSON.stringify({
      type: "clause.offer",
      clause_id: "clause-9",
      text: "真实播放后再确认",
    }));
    expect(clauses.at(-1)).toBe("真实播放后再确认");
    socket.message(finalTtsFrame());
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
      renderer_epoch: 9,
      clause_id: "clause-9",
      complete: true,
    });
    expect(clauses.at(-1)).toBeNull();

    await client.stop();
    expect(stopTrack).toHaveBeenCalledOnce();
    expect(states.at(-1)).toBe("off");
  });
});
