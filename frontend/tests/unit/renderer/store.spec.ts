/**
 * 验证 Renderer store 的普通聊天、附件与非 Task 事件订阅。
 *
 * 测试不构造 Task DTO，确认 optimistic user message 会被服务端消息替换且 preload
 * subscriptions 只包含保留的聊天/桌宠事件。
 */

import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuraBotStore } from "../../../src/app/store";
import type { SendChatMessageResult } from "@shared/contracts/chat";
import type { Message } from "@shared/schemas/message";

function message(id: string, role: Message["role"], content: string): Message {
  return {
    id,
    sessionId: "session-1",
    profileId: "profile-1",
    role,
    content,
    createdAt: "2026-08-19T00:00:00.000Z"
  };
}

describe("useAuraBotStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.unstubAllGlobals();
  });

  it("reconciles an optimistic chat message without Task state", async () => {
    const result: SendChatMessageResult = {
      userMessage: message("user-server", "user", "你好"),
      assistantMessage: message("assistant", "assistant", "我在"),
      agentStatus: "idle"
    };
    vi.stubGlobal("window", {
      aurabot: { sendChatMessage: vi.fn(async () => result) }
    });
    const store = useAuraBotStore();
    store.profile = {
      id: "profile-1",
      displayName: "User",
      active: true,
      createdAt: "now",
      updatedAt: "now"
    };
    store.mainSessionId = "session-1";

    await store.sendMessage("  你好  ");

    expect(store.messages.map((entry) => entry.id)).toEqual(["user-server", "assistant"]);
    expect(store.agentStatus).toBe("idle");
    expect(store).not.toHaveProperty("tasks");
  });

  it("subscribes to retained pet/chat events and updates cursor focus", () => {
    let cursorListener:
      | ((value: { screenX: number; screenY: number; localX: number; localY: number }) => void)
      | undefined;
    const unsubscribe = vi.fn();
    vi.stubGlobal("window", {
      aurabot: {
        onPetSettingsChanged: vi.fn(() => unsubscribe),
        onPetActivity: vi.fn(() => unsubscribe),
        onLive2DModelStateChanged: vi.fn(() => unsubscribe),
        onNotificationSettingsChanged: vi.fn(() => unsubscribe),
        onPetCursorPosition: vi.fn((listener) => {
          cursorListener = listener;
          return unsubscribe;
        }),
        onAgentStatusChanged: vi.fn(() => unsubscribe),
        onChatMessageCreated: vi.fn(() => unsubscribe),
        onChatMessageUpdated: vi.fn(() => unsubscribe)
      }
    });
    const store = useAuraBotStore();

    const stop = store.subscribeToPetEvents();
    cursorListener?.({ screenX: 10, screenY: 20, localX: 3, localY: 4 });

    expect(store.focusAt).toEqual({ x: 3, y: 4 });
    stop();
    expect(unsubscribe).toHaveBeenCalledTimes(8);
  });
});
