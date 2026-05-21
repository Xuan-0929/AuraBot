import { describe, expect, it } from "vitest";
import { ModelBackedGateModel } from "../../../../packages/core-legacy/src/llm/modelBackedGateModel";
import { ModelInvocationError } from "../../../../packages/core-legacy/src/llm/modelErrors";

describe("ModelBackedGateModel", () => {
  it("propagates lightweight model failures instead of using local gate heuristics", async () => {
    const model = new ModelBackedGateModel({
      client: {
        complete: async () => {
          throw new Error("gate unavailable");
        }
      }
    });

    await expect(model.evaluate({
      currentMessage: "帮我写一份实践论的读书笔记",
      recentUserMessages: [],
      recentMessages: []
    })).rejects.toBeInstanceOf(ModelInvocationError);
  });

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

  it("preserves time reminder task subtype from structured gate output", async () => {
    const model = new ModelBackedGateModel({
      client: {
        complete: async () =>
          JSON.stringify({
            replyDecision: "reply",
            inputKind: "task",
            taskKind: "time_reminder",
            memoryDecision: "skip",
            confidence: 0.9,
            reason: "scheduled reminder task",
            coalescedText: "两分钟后提醒我喝水",
            topicEnded: true
          })
      }
    });

    const decision = await model.evaluate({
      currentMessage: "两分钟后提醒我喝水",
      recentUserMessages: [],
      recentMessages: []
    });

    expect(decision).toMatchObject({
      inputKind: "task",
      taskKind: "time_reminder",
      replyDecision: "reply",
      memoryDecision: "skip"
    });
  });

  it("keeps lightweight provider reply decisions even when topic is not ended", async () => {
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
      replyDecision: "reply",
      inputKind: "chat",
      memoryDecision: "skip",
      topicEnded: false,
      reason: "model-gate: normalized provider schema"
    });
  });

  it("keeps direct gate output instead of forcing open chat topics to wait", async () => {
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
      replyDecision: "reply",
      inputKind: "chat",
      memoryDecision: "skip",
      topicEnded: false,
      reason: "needs clarification"
    });
  });

  it("does not rescue clear task requests when the lightweight model under-classifies them", async () => {
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
      replyDecision: "wait",
      inputKind: "chat",
      memoryDecision: "defer",
      topicEnded: false,
      reason: "looks like an unfinished topic"
    });
  });

  it.each(["帮我写一份实践论的读书笔记", "做一份读书笔记", "写一份GitHub周报"])(
    "keeps provider-schema task miss %s without local task override",
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

      expect(decision.inputKind).toBe("chat");
      expect(decision.replyDecision).toBe("wait");
      expect(decision.memoryDecision).toBe("defer");
      expect(decision.topicEnded).toBe(false);
      expect(decision.reason).toBe("unfinished chat");
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

  it.each(["在干嘛", "说两句"])(
    "keeps clear short social prompt %s as wait when the lightweight model waits",
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

      expect(decision.inputKind).toBe("chat");
      expect(decision.replyDecision).toBe("wait");
      expect(decision.memoryDecision).toBe("defer");
      expect(decision.topicEnded).toBe(false);
      expect(decision.reason).toBe("unfinished chat");
    }
  );

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
    expect(messages[0]!.content).toContain("time_reminder");
  });
});
