import { describe, expect, it } from "vitest";
import { ModelBackedGateModel } from "../../../../src/core/llm/modelBackedGateModel";

describe("ModelBackedGateModel", () => {
  it("parses structured gate output from the lightweight model", async () => {
    const model = new ModelBackedGateModel({
      client: {
        complete: async () =>
          JSON.stringify({
            replyDecision: "reply",
            inputKind: "task_control",
            memoryDecision: "skip",
            confidence: 0.91,
            reason: "task-control: main chat command about an existing task",
            coalescedText: "停掉周报任务。",
            topicEnded: true
          })
      }
    });

    const decision = await model.evaluate({
      currentMessage: "停掉周报任务。",
      recentUserMessages: []
    });

    expect(decision.inputKind).toBe("task_control");
    expect(decision.replyDecision).toBe("reply");
    expect(decision.topicEnded).toBe(true);
  });
});
