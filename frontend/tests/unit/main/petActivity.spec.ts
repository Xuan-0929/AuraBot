import { describe, expect, test } from "vitest";
import { buildAssistantMessagePetActivities, buildPetActivity } from "../../../src/main/registerIpc";
import type { AppServices } from "@core/bootstrap/createServices";
import type { SendChatMessageResult } from "@shared/contracts/chat";
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

describe("buildPetActivity", () => {
  test("does not create a duplicate full voice reply bubble after sentence bubbles handle the assistant output", async () => {
    const assistantMessage: Message = {
      id: "assistant-voice-1",
      sessionId: "session-1",
      profileId: "profile-1",
      role: "assistant",
      content: "第一句。第二句。",
      createdAt: "2026-05-05T00:00:00.000Z"
    };
    const result: SendChatMessageResult = {
      userMessage: {
        ...assistantMessage,
        id: "user-voice-1",
        role: "user",
        content: "你说两句。"
      },
      assistantMessage,
      agentStatus: "idle"
    };

    await expect(
      buildPetActivity({} as AppServices, { content: "你说两句。", source: "pet_voice" }, result)
    ).resolves.toBeUndefined();
  });
});
