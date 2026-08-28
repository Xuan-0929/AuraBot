/**
 * 验证 Electron Main 的生产文本实时客户端严格遵守 Main Agent 握手、命令确认与
 * clause 投递顺序；测试只模拟传输边界，不引入浏览器实验客户端的音频与故障工具。
 */

import { describe, expect, it, vi } from "vitest";

import {
  MainAgentRealtimeClient,
  type MainAgentClauseOffer,
  type RealtimeSocket
} from "../../../src/main/mainAgentRealtimeClient";

class FakeSocket implements RealtimeSocket {
  readyState = 0;
  readonly sent: Array<Record<string, unknown>> = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;

  send(data: string): void {
    this.sent.push(JSON.parse(data) as Record<string, unknown>);
  }

  close(): void {
    this.readyState = 3;
    this.onclose?.({ code: 1000, reason: "client_close" } as CloseEvent);
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.({} as Event);
  }

  message(payload: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }
}

function createHarness(onClauseOffer: (offer: MainAgentClauseOffer) => Promise<void> | void = () => undefined) {
  const sockets: FakeSocket[] = [];
  const connections: Array<{ url: string; protocols: string[]; origin: string }> = [];
  let id = 0;
  const fetchImpl = vi.fn(async () => new Response(JSON.stringify({
    token: "one-shot-token",
    expires_in_ms: 30_000,
    subprotocol: "aurabot.main-agent.v1"
  }), { status: 200, headers: { "content-type": "application/json" } }));
  const client = new MainAgentRealtimeClient({
    baseUrl: "http://127.0.0.1:6401",
    fetchImpl,
    createWebSocket(url, protocols, options) {
      connections.push({ url, protocols: [...protocols], origin: options.origin });
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    },
    uuidFactory: () => `00000000-0000-4000-8000-${String(++id).padStart(12, "0")}`,
    onClauseOffer,
    commandTimeoutMs: 1_000
  });
  return { client, sockets, connections, fetchImpl };
}

async function openAndSubmit(harness: ReturnType<typeof createHarness>, text = "remember tea"): Promise<{
  socket: FakeSocket;
  submitting: Promise<void>;
  textCommand: Record<string, unknown>;
}> {
  const submitting = harness.client.submitText(text);
  await vi.waitFor(() => expect(harness.sockets).toHaveLength(1));
  const socket = harness.sockets[0]!;
  socket.open();
  expect(socket.sent[0]).toMatchObject({ type: "session.open", seq: 1, mode: "text" });
  socket.message({
    type: "session.opened",
    renderer_epoch: 7,
    mode: "text",
    client_id: socket.sent[0]?.client_id
  });
  await vi.waitFor(() => expect(socket.sent).toHaveLength(2));
  const textCommand = socket.sent[1]!;
  return { socket, submitting, textCommand };
}

describe("MainAgentRealtimeClient", () => {
  it("acquires a capability, opens the required text session and submits a confirmed command", async () => {
    const harness = createHarness();
    const { socket, submitting, textCommand } = await openAndSubmit(harness);

    expect(harness.fetchImpl).toHaveBeenCalledWith(
      "http://127.0.0.1:6401/api/v1/main-agent/realtime-token",
      { method: "POST", headers: { Origin: "http://127.0.0.1:6400" } }
    );
    expect(harness.connections).toEqual([{
      url: "ws://127.0.0.1:6401/api/v1/main-agent/realtime",
      protocols: ["aurabot.main-agent.v1", "aurabot.capability.one-shot-token"],
      origin: "http://127.0.0.1:6400"
    }]);
    expect(textCommand).toMatchObject({ type: "text.submit", seq: 2, text: "remember tea" });

    socket.message({
      type: "command.ack",
      command_id: textCommand.command_id,
      status: "accepted"
    });

    await expect(submitting).resolves.toBeUndefined();
  });

  it("deduplicates clauses and acknowledges only after the projection callback succeeds", async () => {
    let releaseProjection: (() => void) | undefined;
    const projection = new Promise<void>((resolve) => { releaseProjection = resolve; });
    const onClauseOffer = vi.fn(async () => projection);
    const harness = createHarness(onClauseOffer);
    const { socket, submitting, textCommand } = await openAndSubmit(harness);
    socket.message({ type: "command.ack", command_id: textCommand.command_id, status: "accepted" });
    await submitting;

    const clause = {
      type: "clause.offer",
      renderer_epoch: 7,
      clause_id: "clause-1",
      input_id: "input-1",
      invoke_id: "invoke-1",
      revision: 2,
      text: "I will remember that."
    };
    socket.message(clause);
    socket.message(clause);
    await vi.waitFor(() => expect(onClauseOffer).toHaveBeenCalledTimes(1));
    expect(socket.sent.filter((event) => event.type === "renderer.ack")).toHaveLength(0);

    releaseProjection?.();
    await vi.waitFor(() => expect(socket.sent.filter((event) => event.type === "renderer.ack")).toHaveLength(1));
    expect(socket.sent.at(-1)).toMatchObject({
      type: "renderer.ack",
      seq: 3,
      renderer_epoch: 7,
      clause_id: "clause-1",
      complete: true
    });
  });

  it("surfaces rejected commands and obtains a fresh capability after close", async () => {
    const harness = createHarness();
    const first = await openAndSubmit(harness, "first");
    first.socket.message({
      type: "command.ack",
      command_id: first.textCommand.command_id,
      status: "rejected",
      error: "runtime_unavailable"
    });
    await expect(first.submitting).rejects.toThrow("runtime_unavailable");

    harness.client.close();
    const secondSubmission = harness.client.submitText("second");
    await vi.waitFor(() => expect(harness.sockets).toHaveLength(2));
    const secondSocket = harness.sockets[1]!;
    secondSocket.open();
    secondSocket.message({ type: "session.opened", renderer_epoch: 8, mode: "text" });
    await vi.waitFor(() => expect(secondSocket.sent).toHaveLength(2));
    const secondCommand = secondSocket.sent[1]!;
    secondSocket.message({ type: "command.ack", command_id: secondCommand.command_id, status: "accepted" });

    await expect(secondSubmission).resolves.toBeUndefined();
    expect(harness.fetchImpl).toHaveBeenCalledTimes(2);
  });
});
