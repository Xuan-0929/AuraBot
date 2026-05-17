import { setActivePinia, createPinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuraBotStore } from "../../../src/renderer/src/app/store";
import type { SendChatMessageResult } from "../../../src/shared/contracts/chat";
import type { PetActivity } from "../../../src/shared/contracts/pet";
import type { TaskDetail } from "../../../src/shared/contracts/taskDetail";
import type { Message } from "../../../src/shared/schemas/message";

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });

  return { promise, resolve };
}

describe("useAuraBotStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.unstubAllGlobals();
  });

  it("adds a pending user message while chat IPC is running", async () => {
    const deferred = createDeferred<SendChatMessageResult>();
    vi.stubGlobal("window", {
      aurabot: {
        sendChatMessage: vi.fn(() => deferred.promise)
      }
    });
    const store = useAuraBotStore();
    store.profile = {
      id: "profile-1",
      displayName: "Aura",
      active: true,
      createdAt: "2026-05-04T00:00:00.000Z",
      updatedAt: "2026-05-04T00:00:00.000Z"
    };
    store.mainSessionId = "session-1";

    const sendPromise = store.sendMessage("  我喜欢吃火锅。  ");

    expect(store.messages).toMatchObject([
      {
        sessionId: "session-1",
        profileId: "profile-1",
        role: "user",
        content: "我喜欢吃火锅。"
      }
    ]);
    expect(store.agentStatus).toBe("thinking");

    deferred.resolve({
      userMessage: message("server-user", "user", "我喜欢吃火锅。"),
      assistantMessage: message("server-assistant", "assistant", "我记住了，你喜欢吃火锅。"),
      agentStatus: "idle"
    });
    await sendPromise;

    expect(store.messages.map((item) => item.id)).toEqual(["server-user", "server-assistant"]);
    expect(store.agentStatus).toBe("idle");
  });

  it("removes a task when chat control returns a deleted task id", async () => {
    vi.stubGlobal("window", {
      aurabot: {
        sendChatMessage: vi.fn(() =>
          Promise.resolve({
            userMessage: message("server-user", "user", "删除周报任务"),
            assistantMessage: message("server-assistant", "assistant", "移除了。"),
            agentStatus: "idle",
            deletedTaskId: "task-delete"
          } satisfies SendChatMessageResult)
        )
      }
    });
    const store = useAuraBotStore();
    store.profile = {
      id: "profile-1",
      displayName: "Aura",
      active: true,
      createdAt: "2026-05-04T00:00:00.000Z",
      updatedAt: "2026-05-04T00:00:00.000Z"
    };
    store.mainSessionId = "session-1";
    store.tasks = [
      {
        id: "task-delete",
        profileId: "profile-1",
        parentSessionId: "session-1",
        sessionId: "task-session-1",
        kind: "result_output",
        title: "周报",
        status: "completed",
        latestProgress: "已完成",
        createdAt: "2026-05-04T00:00:00.000Z",
        updatedAt: "2026-05-04T00:00:01.000Z"
      }
    ];

    await store.sendMessage("删除周报任务");

    expect(store.tasks).toEqual([]);
  });


  it("uses one cursor subscription and updates focus from IPC", () => {
    let cursorListener: ((position: { localX: number; localY: number; screenX: number; screenY: number }) => void) | undefined;
    vi.stubGlobal("window", {
      aurabot: {
        onPetSettingsChanged: vi.fn(() => vi.fn()),
        onPetActivity: vi.fn(() => vi.fn()),
        onLive2DModelStateChanged: vi.fn(() => vi.fn()),
        onNotificationSettingsChanged: vi.fn(() => vi.fn()),
        onPetCursorPosition: vi.fn((listener: typeof cursorListener) => {
          cursorListener = listener;
          return vi.fn();
        }),
        onAgentStatusChanged: vi.fn(() => vi.fn()),
        onChatMessageCreated: vi.fn(() => vi.fn()),
        onChatMessageUpdated: vi.fn(() => vi.fn()),
        onTaskChanged: vi.fn(() => vi.fn()),
        onTaskDeleted: vi.fn(() => vi.fn())
      }
    });
    const store = useAuraBotStore();

    const unsubscribe = store.subscribeToPetEvents();
    cursorListener?.({ screenX: 100, screenY: 200, localX: 12, localY: 34 });

    expect(window.aurabot.onPetCursorPosition).toHaveBeenCalledTimes(1);
    expect(store.focusAt).toEqual({ x: 12, y: 34 });

    unsubscribe();
  });

  it("prioritizes task activities over stale chat bubbles", () => {
    const store = useAuraBotStore();
    const chat: PetActivity = {
      id: "chat-1",
      kind: "chat_reply",
      message: "第一句。",
      createdAt: "2026-05-05T00:00:01.000Z"
    };
    const task: PetActivity = {
      id: "task-completed",
      kind: "task_completed",
      message: "任务完成了。",
      createdAt: "2026-05-05T00:00:02.000Z"
    };

    store.enqueuePetActivity(chat);
    store.enqueuePetActivity(task);

    expect(store.petActivity).toEqual(task);
    expect(store.petActivityQueue).toEqual([]);
  });

  it("loads and clears selected task details", async () => {
    const detail: TaskDetail = {
      task: {
        id: "task-1",
        profileId: "profile-1",
        parentSessionId: "session-1",
        sessionId: "task-session-1",
        kind: "result_output",
        title: "整理任务详情",
        status: "completed",
        latestProgress: "已完成",
        createdAt: "2026-05-16T00:00:00.000Z",
        updatedAt: "2026-05-16T00:00:01.000Z"
      },
      inputArtifacts: [],
      artifacts: [],
      timeline: []
    };
    vi.stubGlobal("window", {
      aurabot: {
        getTaskDetail: vi.fn(() => Promise.resolve(detail))
      }
    });
    const store = useAuraBotStore();

    await store.openTaskDetail("task-1");

    expect(window.aurabot.getTaskDetail).toHaveBeenCalledWith({ taskId: "task-1" });
    expect(store.selectedTaskDetail).toEqual(detail);
    expect(store.selectedTaskDetailTaskId).toBe("task-1");

    store.closeTaskDetail();

    expect(store.selectedTaskDetail).toBeUndefined();
    expect(store.selectedTaskDetailTaskId).toBeUndefined();
  });
});

function message(id: string, role: Message["role"], content: string): Message {
  return {
    id,
    sessionId: "session-1",
    profileId: "profile-1",
    role,
    content,
    createdAt: "2026-05-04T00:00:01.000Z"
  };
}
