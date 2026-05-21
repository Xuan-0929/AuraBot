import { describe, expect, it } from "vitest";
import { evaluateGate } from "../../../../packages/core-legacy/src/orchestration/gateEvaluator";

describe("evaluateGate", () => {
  it("waits on short fragmented chat input and defers memory writes", () => {
    const decision = evaluateGate({
      currentMessage: "然后我觉得",
      recentUserMessages: []
    });

    expect(decision.replyDecision).toBe("wait");
    expect(decision.memoryDecision).toBe("defer");
    expect(decision.inputKind).toBe("chat");
    expect(decision.reason).toContain("fragment");
    expect(decision.topicEnded).toBe(false);
  });

  it("replies and allows memory write when the utterance has a clear boundary", () => {
    const decision = evaluateGate({
      currentMessage: "我喜欢吃火锅。",
      recentUserMessages: []
    });

    expect(decision.replyDecision).toBe("reply");
    expect(decision.memoryDecision).toBe("write_now");
    expect(decision.inputKind).toBe("chat");
    expect(decision.confidence).toBeGreaterThanOrEqual(0.7);
    expect(decision.topicEnded).toBe(true);
  });

  it("classifies clear task requests separately from chat", () => {
    const decision = evaluateGate({
      currentMessage: "帮我整理一份明天会议的清单。",
      recentUserMessages: []
    });

    expect(decision.replyDecision).toBe("reply");
    expect(decision.memoryDecision).toBe("skip");
    expect(decision.inputKind).toBe("task");
    expect(decision.taskKind).toBe("result_output");
  });

  it.each(["两分钟后提醒我喝水", "提醒我两分钟后喝水"])(
    "classifies scheduled reminder request %s as a time reminder task",
    (content) => {
      const decision = evaluateGate({
        currentMessage: content,
        recentUserMessages: []
      });

      expect(decision.replyDecision).toBe("reply");
      expect(decision.memoryDecision).toBe("skip");
      expect(decision.inputKind).toBe("task");
      expect(decision.taskKind).toBe("time_reminder");
      expect(decision.topicEnded).toBe(true);
    }
  );

  it("classifies writing a reading note as a task even without sentence punctuation", () => {
    const decision = evaluateGate({
      currentMessage: "帮我写一份实践论的读书笔记",
      recentUserMessages: []
    });

    expect(decision.replyDecision).toBe("reply");
    expect(decision.memoryDecision).toBe("skip");
    expect(decision.inputKind).toBe("task");
    expect(decision.topicEnded).toBe(true);
  });

  it.each(["做一份读书笔记", "写一份GitHub周报"])(
    "classifies direct creation request %s as a task without requiring a helper prefix",
    (content) => {
      const decision = evaluateGate({
        currentMessage: content,
        recentUserMessages: []
      });

      expect(decision.replyDecision).toBe("reply");
      expect(decision.memoryDecision).toBe("skip");
      expect(decision.inputKind).toBe("task");
      expect(decision.topicEnded).toBe(true);
    }
  );

  it("classifies conversational task controls separately from new task requests", () => {
    const decision = evaluateGate({
      currentMessage: "先停掉这个任务。",
      recentUserMessages: []
    });

    expect(decision.replyDecision).toBe("reply");
    expect(decision.memoryDecision).toBe("skip");
    expect(decision.inputKind).toBe("task_control");
  });

  it("classifies main-chat task start commands as task controls", () => {
    const decision = evaluateGate({
      currentMessage: "开始做这个任务。",
      recentUserMessages: []
    });

    expect(decision.replyDecision).toBe("reply");
    expect(decision.memoryDecision).toBe("skip");
    expect(decision.inputKind).toBe("task_control");
  });

  it.each(["重试周报任务", "打开周报结果", "删除已完成的周报任务", "把喝水提醒改到明天早上九点"])(
    "classifies enhanced task control command %s as task control",
    (content) => {
      const decision = evaluateGate({
        currentMessage: content,
        recentUserMessages: []
      });

      expect(decision.replyDecision).toBe("reply");
      expect(decision.memoryDecision).toBe("skip");
      expect(decision.inputKind).toBe("task_control");
    }
  );

  it("replies to a follow-up when recent fragments form enough context", () => {
    const decision = evaluateGate({
      currentMessage: "我喜欢吃火锅。",
      recentUserMessages: ["我昨天想了想", "还是得告诉你"]
    });

    expect(decision.replyDecision).toBe("reply");
    expect(decision.memoryDecision).toBe("write_now");
    expect(decision.coalescedText).toContain("我昨天想了想");
    expect(decision.coalescedText).toContain("火锅");
  });
});
