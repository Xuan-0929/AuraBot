/**
 * 模型 Gate 单元测试：覆盖 chat/task 结构化结果、provider schema 归一化、上下文提示
 * 和模型错误包装。最小 task 意图只用于迁移期不可用回复，不含控制或提醒 subtype。
 */
import { describe, expect, it } from "vitest";
import { ModelBackedGateModel } from "@core/llm/modelBackedGateModel";
import { ModelInvocationError } from "@core/llm/modelErrors";

describe("ModelBackedGateModel", () => {
  it("propagates model failures", async () => {
    const model = new ModelBackedGateModel({ client: { complete: async () => { throw new Error("unavailable"); } } });
    await expect(model.evaluate({ currentMessage: "你好", recentUserMessages: [], recentMessages: [] }))
      .rejects.toBeInstanceOf(ModelInvocationError);
  });

  it("parses the minimal task intent without legacy subtype fields", async () => {
    const model = new ModelBackedGateModel({
      client: { complete: async () => JSON.stringify({
        replyDecision: "reply",
        inputKind: "task",
        memoryDecision: "skip",
        confidence: 0.91,
        reason: "execution request",
        coalescedText: "帮我写一份周报",
        topicEnded: true
      }) }
    });

    await expect(model.evaluate({ currentMessage: "帮我写一份周报", recentUserMessages: [], recentMessages: [] }))
      .resolves.toEqual({
        replyDecision: "reply",
        inputKind: "task",
        memoryDecision: "skip",
        confidence: 0.91,
        reason: "execution request",
        coalescedText: "帮我写一份周报",
        topicEnded: true
      });
  });

  it("normalizes provider task output to a reply with no memory write", async () => {
    const model = new ModelBackedGateModel({
      client: { complete: async () => JSON.stringify({ isTask: true, shouldReply: false, memory: { shouldWrite: true } }) }
    });
    const decision = await model.evaluate({ currentMessage: "提醒我喝水", recentUserMessages: [], recentMessages: [] });
    expect(decision).toMatchObject({ inputKind: "task", replyDecision: "reply", memoryDecision: "skip", topicEnded: true });
  });

  it("enforces the unavailable-task invariant on contradictory structured output", async () => {
    const model = new ModelBackedGateModel({
      client: { complete: async () => JSON.stringify({
        replyDecision: "wait",
        inputKind: "task",
        memoryDecision: "write_now",
        confidence: 0.88,
        reason: "contradictory provider output",
        coalescedText: "帮我运行任务",
        topicEnded: false
      }) }
    });

    await expect(model.evaluate({
      currentMessage: "帮我运行任务",
      recentUserMessages: [],
      recentMessages: []
    })).resolves.toMatchObject({
      inputKind: "task",
      replyDecision: "reply",
      memoryDecision: "skip",
      topicEnded: true
    });
  });

  it("includes recent user and assistant messages in the prompt", async () => {
    let request: unknown;
    const model = new ModelBackedGateModel({
      client: { complete: async (input) => {
        request = input;
        return JSON.stringify({
          replyDecision: "reply", inputKind: "chat", memoryDecision: "skip", confidence: 0.8,
          reason: "context available", coalescedText: "继续", topicEnded: true
        });
      } }
    });
    await model.evaluate({
      currentMessage: "继续",
      recentUserMessages: ["前一句"],
      recentMessages: [{ role: "user", content: "前一句" }, { role: "assistant", content: "我在听" }]
    });
    const messages = (request as { messages: Array<{ content: string }> }).messages;
    expect(JSON.parse(messages[1]!.content).recentMessages).toHaveLength(2);
    expect(messages[0]!.content).toContain("inputKind(chat|task)");
    expect(messages[0]!.content).not.toContain("task_control");
  });
});
