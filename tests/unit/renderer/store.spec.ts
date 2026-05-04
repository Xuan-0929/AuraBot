import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { useAuraBotStore } from "../../../src/renderer/src/app/store";
import type { SendChatMessageResult } from "../../../src/shared/contracts/chat";
import type { Message } from "../../../src/shared/schemas/message";
import type { Task } from "../../../src/shared/schemas/task";

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

  test("applies task and assistant message broadcasts from the main process", () => {
    let chatMessageListener: ((message: Message) => void) | undefined;
    let taskChangedListener: ((task: Task) => void) | undefined;
    vi.stubGlobal("window", {
      aurabot: {
        onPetSettingsChanged: vi.fn(() => vi.fn()),
        onPetActivity: vi.fn(() => vi.fn()),
        onAgentStatusChanged: vi.fn(() => vi.fn()),
        onChatMessageCreated: vi.fn((listener: (message: Message) => void) => {
          chatMessageListener = listener;

          return vi.fn();
        }),
        onTaskChanged: vi.fn((listener: (task: Task) => void) => {
          taskChangedListener = listener;

          return vi.fn();
        })
      }
    });

    const unsubscribe = useAuraBotStore.getState().subscribeToPetEvents();
    const assistantMessage: Message = {
      id: "assistant-task-completed",
      sessionId: "session-1",
      profileId: "profile-1",
      role: "assistant",
      content: "这个任务完成了：GitHub 周报已经写好。",
      createdAt: "2026-05-04T00:00:03.000Z"
    };
    const runningTask: Task = {
      id: "task-1",
      profileId: "profile-1",
      parentSessionId: "session-1",
      sessionId: "task-session-1",
      title: "帮我做一份 GitHub 周报。",
      status: "running",
      latestProgress: "开始调用 Codex 子任务：帮我做一份 GitHub 周报。",
      createdAt: "2026-05-04T00:00:01.000Z",
      updatedAt: "2026-05-04T00:00:02.000Z"
    };

    chatMessageListener?.(assistantMessage);
    chatMessageListener?.(assistantMessage);
    taskChangedListener?.(runningTask);
    taskChangedListener?.({
      ...runningTask,
      status: "completed",
      latestProgress: "Codex 子任务完成：GitHub 周报已经写好。",
      updatedAt: "2026-05-04T00:00:04.000Z"
    });

    expect(useAuraBotStore.getState().messages).toEqual([assistantMessage]);
    expect(useAuraBotStore.getState().tasks).toHaveLength(1);
    expect(useAuraBotStore.getState().tasks[0]).toMatchObject({
      id: "task-1",
      status: "completed",
      latestProgress: "Codex 子任务完成：GitHub 周报已经写好。"
    });

    unsubscribe();
  });
});
