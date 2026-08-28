/**
 * 验证 chat IPC 的纯路由边界只调用注入的 Python Main Agent 所有者，并在返回后广播
 * 状态；旧 AppServices/ChatOrchestrator 不属于该函数的输入，因此无法成为隐式 fallback。
 */

import { describe, expect, it, vi } from "vitest";

import { sendChatMessageThroughOwner } from "../../../src/main/registerIpc";

describe("sendChatMessageThroughOwner", () => {
  it("records activity, delegates once to the injected owner and broadcasts its status", async () => {
    const result = {
      userMessage: {
        id: "message-user",
        sessionId: "session-main",
        profileId: "profile-main",
        role: "user" as const,
        content: "hello",
        createdAt: "2026-08-28T00:00:00.000Z"
      },
      agentStatus: "thinking" as const
    };
    const owner = {
      recordUserActivity: vi.fn(),
      sendChatMessage: vi.fn(async () => result),
      broadcastAgentStatus: vi.fn()
    };

    await expect(sendChatMessageThroughOwner(owner, { content: "hello" })).resolves.toBe(result);
    expect(owner.recordUserActivity).toHaveBeenCalledTimes(1);
    expect(owner.sendChatMessage).toHaveBeenCalledWith({ content: "hello" });
    expect(owner.broadcastAgentStatus).toHaveBeenCalledWith("thinking");
  });
});
