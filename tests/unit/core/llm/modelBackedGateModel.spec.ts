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

  it("normalizes lightweight provider gate fields into AuraBot gate decisions", async () => {
    const model = new ModelBackedGateModel({
      client: {
        complete: async () =>
          JSON.stringify({
            shouldReply: true,
            isTask: false,
            isTaskControl: false,
            memory: {
              shouldWrite: false
            },
            isTopicEnded: false
          })
      }
    });

    const decision = await model.evaluate({
      currentMessage: "然后我觉得",
      recentUserMessages: []
    });

    expect(decision).toMatchObject({
      replyDecision: "wait",
      inputKind: "chat",
      memoryDecision: "defer",
      topicEnded: false,
      reason: "model-gate: normalized provider schema"
    });
  });

  it("guards against direct gate output replying before a chat topic has ended", async () => {
    const model = new ModelBackedGateModel({
      client: {
        complete: async () =>
          JSON.stringify({
            replyDecision: "reply",
            inputKind: "chat",
            memoryDecision: "skip",
            confidence: 0.66,
            reason: "needs clarification",
            coalescedText: "然后我觉得",
            topicEnded: false
          })
      }
    });

    const decision = await model.evaluate({
      currentMessage: "然后我觉得",
      recentUserMessages: []
    });

    expect(decision).toMatchObject({
      replyDecision: "wait",
      inputKind: "chat",
      memoryDecision: "defer",
      topicEnded: false,
      reason: "model-gate: chat topic still open"
    });
  });
});
