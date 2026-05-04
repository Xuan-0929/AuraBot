import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { useAuraBotStore } from "../../../src/renderer/src/app/store";
import type { SendChatMessageResult } from "../../../src/shared/contracts/chat";

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });

  return { promise, resolve };
}

describe("useAuraBotStore chat sending", () => {
  beforeEach(() => {
    useAuraBotStore.setState({
      profile: {
        id: "profile-1",
        displayName: "Aura",
        active: true,
        createdAt: "2026-05-04T00:00:00.000Z",
        updatedAt: "2026-05-04T00:00:00.000Z"
      },
      mainSessionId: "session-1",
      messages: [],
      tasks: [],
      agentStatus: "idle",
      petActivity: undefined
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("adds the user message immediately before IPC completes", async () => {
    const deferred = createDeferred<SendChatMessageResult>();
    const sendChatMessage = vi.fn(() => deferred.promise);
    vi.stubGlobal("window", {
      aurabot: {
        sendChatMessage
      }
    });

    const sendPromise = useAuraBotStore.getState().sendMessage("  我喜欢吃火锅。  ");

    expect(useAuraBotStore.getState().messages).toMatchObject([
      {
        sessionId: "session-1",
        profileId: "profile-1",
        role: "user",
        content: "我喜欢吃火锅。"
      }
    ]);
    expect(useAuraBotStore.getState().messages[0]?.id).toMatch(/^local-pending-/);
    expect(useAuraBotStore.getState().agentStatus).toBe("thinking");

    deferred.resolve({
      userMessage: {
        id: "server-user",
        sessionId: "session-1",
        profileId: "profile-1",
        role: "user",
        content: "我喜欢吃火锅。",
        createdAt: "2026-05-04T00:00:01.000Z"
      },
      assistantMessage: {
        id: "server-assistant",
        sessionId: "session-1",
        profileId: "profile-1",
        role: "assistant",
        content: "我记住了，你喜欢吃火锅。",
        createdAt: "2026-05-04T00:00:02.000Z"
      },
      agentStatus: "idle"
    });
    await sendPromise;

    expect(useAuraBotStore.getState().messages.map((message) => message.id)).toEqual([
      "server-user",
      "server-assistant"
    ]);
  });
});
