/**
 * 验证 Task 迁移空窗期的唯一用户可见行为。
 *
 * 测试使用真实 ChatOrchestrator、SQLite 和 Gate 注入，断言 Task 意图只写普通主会话
 * assistant 回复，且不会创建 Task schema、child session 或 task.* event。
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatOrchestrator } from "@core/orchestration/chatOrchestrator";
import { cleanupTestServices, createTestServices } from "../../helpers/testServices";

afterEach(cleanupTestServices);

describe("chat task migration gap", () => {
  it("returns the fixed unavailable reply without Task side effects", async () => {
    const services = createTestServices("aurabot-task-unavailable-");
    const orchestrator = new ChatOrchestrator({
      ...services,
      gateModel: {
        evaluate: async (input) => ({
          replyDecision: "reply",
          inputKind: "task",
          memoryDecision: "skip",
          confidence: 1,
          reason: "explicit task request",
          coalescedText: input.currentMessage,
          topicEnded: true
        })
      }
    });

    const onAssistantMessageCreated = vi.fn();
    const result = await orchestrator.sendMessage(
      { content: "帮我整理桌面文件" },
      { onAssistantMessageCreated }
    );
    const sessions = services.database
      .prepare("select id from sessions order by created_at")
      .all() as Array<{ id: string }>;
    const taskTables = services.database
      .prepare(
        "select name from sqlite_master where type = 'table' and name in (?, ?, ?, ?) order by name"
      )
      .all("tasks", "task_events", "task_artifacts", "task_input_artifacts");
    const taskEvents = services.database
      .prepare("select event_type from event_logs where event_type like 'task.%'")
      .all();

    expect(result.assistantMessage?.content).toBe("Task 功能正在迁移，当前暂不可用。");
    expect(onAssistantMessageCreated).toHaveBeenCalledTimes(1);
    expect(onAssistantMessageCreated).toHaveBeenCalledWith(result.assistantMessage);
    expect(result.agentStatus).toBe("idle");
    expect(sessions).toHaveLength(1);
    expect(taskTables).toEqual([]);
    expect(taskEvents).toEqual([]);
  });
});
