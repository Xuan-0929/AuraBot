/**
 * 桌宠活动测试：验证聊天、Memory 与 idle 主动活动到展示消息的映射。
 * 用例只覆盖当前 PetActivity 契约，不构造已删除的执行完成或定时提醒活动。
 */
import { describe, expect, test } from "vitest";
import { buildAssistantMessagePetActivities } from "../../../src/main/registerIpc";
import type { Message } from "@shared/schemas/message";

describe("buildAssistantMessagePetActivities", () => {
  test("splits normal assistant chat output into sentence-level pet message bubble activities", () => {
    const message: Message = {
      id: "assistant-1",
      sessionId: "session-1",
      profileId: "profile-1",
      role: "assistant",
      content: "我在，刚才那件事我已经记住了。你继续说。",
      createdAt: "2026-05-05T00:00:00.000Z"
    };

    expect(buildAssistantMessagePetActivities(message, { startIndex: 0 })).toMatchObject({
      nextIndex: message.content.length,
      activities: [
        {
          kind: "chat_reply",
          message: "我在，刚才那件事我已经记住了。"
        },
        {
          kind: "chat_reply",
          message: "你继续说。"
        }
      ]
    });
  });

  test("emits only newly completed sentences while a reply streams", () => {
    const message: Message = {
      id: "assistant-1",
      sessionId: "session-1",
      profileId: "profile-1",
      role: "assistant",
      content: "第一句好了。第二句还在写",
      createdAt: "2026-05-05T00:00:00.000Z"
    };

    const firstPass = buildAssistantMessagePetActivities(message, { startIndex: 0 });
    expect(firstPass).toMatchObject({
      nextIndex: 6,
      activities: [
        {
          message: "第一句好了。"
        }
      ]
    });

    expect(
      buildAssistantMessagePetActivities(
        {
          ...message,
          content: "第一句好了。第二句还在写。"
        },
        { startIndex: firstPass.nextIndex }
      )
    ).toMatchObject({
      nextIndex: "第一句好了。第二句还在写。".length,
      activities: [
        {
          message: "第二句还在写。"
        }
      ]
    });
  });

  test("flushes a final sentence even when it has no punctuation", () => {
    const message: Message = {
      id: "assistant-1",
      sessionId: "session-1",
      profileId: "profile-1",
      role: "assistant",
      content: "我在这儿",
      createdAt: "2026-05-05T00:00:00.000Z"
    };

    expect(buildAssistantMessagePetActivities(message, { startIndex: 0, flushRemainder: true })).toMatchObject({
      nextIndex: message.content.length,
      activities: [
        {
          message: "我在这儿"
        }
      ]
    });
  });

  test("ignores non-assistant and empty assistant messages", () => {
    const userMessage: Message = {
      id: "user-1",
      sessionId: "session-1",
      profileId: "profile-1",
      role: "user",
      content: "你好",
      createdAt: "2026-05-05T00:00:00.000Z"
    };
    const emptyAssistantMessage: Message = {
      ...userMessage,
      id: "assistant-empty",
      role: "assistant",
      content: "   "
    };

    expect(buildAssistantMessagePetActivities(userMessage, { startIndex: 0 }).activities).toEqual([]);
    expect(buildAssistantMessagePetActivities(emptyAssistantMessage, { startIndex: 0 }).activities).toEqual([]);
  });
});
