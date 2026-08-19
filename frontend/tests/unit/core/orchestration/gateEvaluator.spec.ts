/**
 * 验证确定性 Gate 只输出 chat/task，并将所有 Task 语义送往 unavailable 分支。
 *
 * 测试覆盖普通聊天碎片、直接社交回复、执行请求与提醒/控制表达，不依赖模型 mock。
 */

import { describe, expect, it } from "vitest";
import { evaluateGate } from "@core/orchestration/gateEvaluator";

describe("evaluateGate", () => {
  it("waits for a chat fragment", () => {
    expect(
      evaluateGate({ currentMessage: "然后我觉得", recentUserMessages: [] })
    ).toMatchObject({
      replyDecision: "wait",
      inputKind: "chat",
      memoryDecision: "defer"
    });
  });

  it.each(["帮我整理桌面", "提醒我明天喝水", "这个任务取消掉"])(
    "classifies %s as the minimal task intent",
    (currentMessage) => {
      expect(evaluateGate({ currentMessage, recentUserMessages: [] })).toMatchObject({
        replyDecision: "reply",
        inputKind: "task",
        memoryDecision: "skip",
        topicEnded: true
      });
    }
  );

  it("replies to a direct social prompt as chat", () => {
    expect(
      evaluateGate({ currentMessage: "在干嘛", recentUserMessages: [] })
    ).toMatchObject({ replyDecision: "reply", inputKind: "chat" });
  });
});
