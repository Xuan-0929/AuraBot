import { describe, expect, it, vi } from "vitest";
import { loadMainSessionMessagesBackendFirst } from "../../../desktop/src/main/registerIpc";
import type { Message } from "../../../packages/shared/src/schemas/message";

describe("loadMainSessionMessagesBackendFirst", () => {
  it("returns backend main session messages when the backend read succeeds", async () => {
    const backendMessage = message("backend-message", "后端消息");
    const backendReader = vi.fn(() =>
      Promise.resolve({
        sessionId: "session-main",
        messages: [backendMessage]
      })
    );
    const localReader = vi.fn(() => []);

    const messages = await loadMainSessionMessagesBackendFirst({
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1",
      sessionId: "session-main",
      backendReader,
      localReader
    });

    expect(messages).toEqual([backendMessage]);
    expect(backendReader).toHaveBeenCalledWith({
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1"
    });
    expect(localReader).not.toHaveBeenCalled();
  });

  it("falls back to local main session messages when the backend read fails", async () => {
    const fallbackMessage = message("local-message", "本地消息");
    const backendReader = vi.fn(() => Promise.reject(new Error("offline")));
    const localReader = vi.fn(() => [fallbackMessage]);

    const messages = await loadMainSessionMessagesBackendFirst({
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1",
      sessionId: "session-main",
      backendReader,
      localReader
    });

    expect(messages).toEqual([fallbackMessage]);
    expect(localReader).toHaveBeenCalledWith("session-main");
  });
});

function message(id: string, content: string): Message {
  return {
    id,
    sessionId: "session-main",
    profileId: "profile-1",
    role: "user",
    content,
    attachments: [],
    createdAt: "2026-05-21T00:00:00.000Z"
  };
}
