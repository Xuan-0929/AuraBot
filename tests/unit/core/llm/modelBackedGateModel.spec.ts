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
      recentUserMessages: [],
      recentMessages: []
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
      recentUserMessages: [],
      recentMessages: []
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
      recentUserMessages: [],
      recentMessages: []
    });

    expect(decision).toMatchObject({
      replyDecision: "wait",
      inputKind: "chat",
      memoryDecision: "defer",
      topicEnded: false,
      reason: "model-gate: chat topic still open"
    });
  });

  it("rescues clear task requests when the lightweight model under-classifies them as open chat", async () => {
    const model = new ModelBackedGateModel({
      client: {
        complete: async () =>
          JSON.stringify({
            replyDecision: "wait",
            inputKind: "chat",
            memoryDecision: "defer",
            confidence: 0.62,
            reason: "looks like an unfinished topic",
            coalescedText: "帮我写一份实践论的读书笔记",
            topicEnded: false
          })
      }
    });

    const decision = await model.evaluate({
      currentMessage: "帮我写一份实践论的读书笔记",
      recentUserMessages: [],
      recentMessages: []
    });

    expect(decision).toMatchObject({
      replyDecision: "reply",
      inputKind: "task",
      memoryDecision: "skip",
      topicEnded: true
    });
    expect(decision.reason).toContain("local-task-guard");
  });

  it.each(["帮我写一份实践论的读书笔记", "做一份读书笔记", "写一份GitHub周报"])(
    "rescues provider-schema task miss %s with the same local task guard",
    async (content) => {
      const model = new ModelBackedGateModel({
        client: {
          complete: async () =>
            JSON.stringify({
              shouldReply: false,
              isTask: false,
              isTaskControl: false,
              memory: {
                shouldWrite: false
              },
              isTopicEnded: false,
              reason: "unfinished chat"
            })
        }
      });

      const decision = await model.evaluate({
        currentMessage: content,
        recentUserMessages: [],
        recentMessages: []
      });

      expect(decision.inputKind).toBe("task");
      expect(decision.replyDecision).toBe("reply");
      expect(decision.memoryDecision).toBe("skip");
      expect(decision.topicEnded).toBe(true);
      expect(decision.reason).toContain("local-task-guard");
    }
  );

  it("keeps non-task provider-schema chat misses as listening", async () => {
    const model = new ModelBackedGateModel({
      client: {
        complete: async () =>
          JSON.stringify({
            shouldReply: false,
            isTask: false,
            isTaskControl: false,
            memory: {
              shouldWrite: false
            },
            isTopicEnded: false,
            reason: "unfinished chat"
          })
      }
    });

    const decision = await model.evaluate({
      currentMessage: "然后我觉得",
      recentUserMessages: [],
      recentMessages: []
    });

    expect(decision.inputKind).toBe("chat");
    expect(decision.replyDecision).toBe("wait");
    expect(decision.memoryDecision).toBe("defer");
    expect(decision.topicEnded).toBe(false);
  });

  it("includes recent assistant and user messages in the lightweight gate prompt", async () => {
    let request: unknown;
    const model = new ModelBackedGateModel({
      client: {
        complete: async (input) => {
          request = input;

          return JSON.stringify({
            replyDecision: "reply",
            inputKind: "chat",
            memoryDecision: "skip",
            confidence: 0.8,
            reason: "enough context",
            coalescedText: "我继续讲。",
            topicEnded: true
          });
        }
      }
    });

    await model.evaluate({
      currentMessage: "我继续讲。",
      recentUserMessages: ["前一句"],
      recentMessages: [
        { role: "user", content: "前一句" },
        { role: "assistant", content: "我在听。" }
      ]
    });

    const messages = (request as { messages: Array<{ role: string; content: string }> }).messages;
    const userPayload = JSON.parse(messages[1]!.content) as {
      recentMessages: Array<{ role: string; content: string }>;
    };

    expect(userPayload.recentMessages).toEqual([
      { role: "user", content: "前一句" },
      { role: "assistant", content: "我在听。" }
    ]);
    expect(messages[0]!.content).toContain("读书笔记");
    expect(messages[0]!.content).toContain("inputKind=task");
  });
});
