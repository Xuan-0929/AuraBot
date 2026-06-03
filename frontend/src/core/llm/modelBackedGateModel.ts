import { z } from "zod";
import type { GateDecision, InputKind, MemoryDecision, ReplyDecision } from "@shared/contracts/chat";
import type { TaskKind } from "@shared/schemas/task";
import type { GateEvaluationInput } from "../orchestration/gateEvaluator";
import type { ModelRoleConfig } from "@shared/schemas/config";
import type { ChatCompletionRequest } from "./openAiCompatibleClient";
import { ModelInvocationError } from "./modelErrors";

export interface GateModelClient {
  complete(input: ChatCompletionRequest): Promise<string>;
}

export interface ModelBackedGateModelOptions {
  client: GateModelClient;
  config?: ModelRoleConfig;
}

export const structuredGateDecisionSchema = z.object({
  replyDecision: z.enum(["reply", "wait"]),
  inputKind: z.enum(["chat", "task", "task_control"]),
  taskKind: z.enum(["result_output", "time_reminder"]).optional(),
  memoryDecision: z.enum(["write_now", "defer", "skip"]),
  confidence: z.number().min(0).max(1),
  reason: z.string().min(1),
  coalescedText: z.string().min(1),
  topicEnded: z.boolean().optional()
});

const providerGateDecisionSchema = z
  .object({
    shouldReply: z.boolean().optional(),
    isTask: z.boolean().optional(),
    taskKind: z.enum(["result_output", "time_reminder"]).optional(),
    isTimeReminderTask: z.boolean().optional(),
    isResultOutputTask: z.boolean().optional(),
    isTaskControl: z.boolean().optional(),
    memory: z
      .object({
        shouldWrite: z.boolean().optional()
      })
      .optional(),
    isTopicEnded: z.boolean().optional(),
    confidence: z.number().min(0).max(1).optional(),
    reason: z.string().min(1).optional()
  })
  .passthrough()
  .refine(
    (value) =>
      value.shouldReply !== undefined ||
      value.isTask !== undefined ||
      value.isTaskControl !== undefined ||
      value.memory?.shouldWrite !== undefined ||
      value.isTopicEnded !== undefined,
    "Provider gate decision did not include recognizable fields"
  );

export class ModelBackedGateModel {
  constructor(private readonly options: ModelBackedGateModelOptions) {}

  async evaluate(input: GateEvaluationInput): Promise<GateDecision> {
    try {
      const content = await this.options.client.complete({
        baseUrl: this.options.config?.baseUrl ?? "",
        apiKey: this.options.config?.apiKey ?? "",
        model: this.options.config?.model ?? "gate-model",
        temperature: this.options.config?.temperature ?? 0,
        timeoutMs: this.options.config?.timeoutMs,
        messages: buildGatePrompt(input)
      });
      return normalizeGateDecision(parseJsonFromModel(content), input);
    } catch (error) {
      throw new ModelInvocationError("gateModel", error);
    }
  }
}

function buildGatePrompt(input: GateEvaluationInput): ChatCompletionRequest["messages"] {
  return [
    {
      role: "system",
      content: [
        "你是 AuraBot 的轻量判断模型。只返回 JSON，不要解释。",
        "字段必须是 replyDecision(reply|wait), inputKind(chat|task|task_control), memoryDecision(write_now|defer|skip), confidence(0-1), reason, coalescedText, topicEnded；当 inputKind=task 时必须额外给 taskKind(result_output|time_reminder)。",
        "输入里 recentMessages 是最近完整对话上下文，包含 user/assistant，请结合它判断用户是在继续上一句话、回应 Aura、提出新问题，还是发起/控制任务。",
        "任务判断要宽松：只要用户要求 Aura 帮他产出、整理、撰写、生成、查询、搜索、总结、翻译、做报告、周报、读书笔记、清单、文档或表格，就判为 inputKind=task、taskKind=result_output、replyDecision=reply、memoryDecision=skip、topicEnded=true，即使句末没有标点。",
        "如果用户要求在某个时间或一段时间后提醒他，例如“两分钟后提醒我喝水”，判为 inputKind=task、taskKind=time_reminder、replyDecision=reply、memoryDecision=skip、topicEnded=true。",
        "普通聊天是否回复由你根据上下文判断；短句、没标点、topicEnded=false 都不能单独作为必须等待的理由。"
      ].join("\n")
    },
    {
      role: "user",
      content: JSON.stringify(input)
    }
  ];
}

function parseJsonFromModel(content: string): unknown {
  const trimmed = content.trim();
  if (trimmed.startsWith("{")) {
    return JSON.parse(trimmed);
  }

  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  if (fenced?.[1]) {
    return JSON.parse(fenced[1]);
  }

  throw new Error("Gate model did not return JSON");
}

function normalizeGateDecision(rawDecision: unknown, input: GateEvaluationInput): GateDecision {
  const directDecision = structuredGateDecisionSchema.safeParse(rawDecision);

  if (directDecision.success) {
    return normalizeDirectGateDecision(directDecision.data);
  }

  const providerDecision = providerGateDecisionSchema.parse(rawDecision);
  const inputKind = normalizeInputKind(providerDecision);
  const taskKind = normalizeTaskKind(inputKind, providerDecision);
  const topicEnded = providerDecision.isTopicEnded ?? inferTopicEnded(inputKind, providerDecision.shouldReply);
  const replyDecision = normalizeReplyDecision({
    inputKind,
    shouldReply: providerDecision.shouldReply,
    topicEnded: providerDecision.isTopicEnded
  });
  const memoryDecision = normalizeMemoryDecision({
    inputKind,
    replyDecision,
    shouldWrite: providerDecision.memory?.shouldWrite
  });

  return {
    replyDecision,
    inputKind,
    memoryDecision,
    confidence: providerDecision.confidence ?? estimateNormalizedConfidence({ inputKind, replyDecision, memoryDecision }),
    reason: providerDecision.reason ?? "model-gate: normalized provider schema",
    coalescedText: buildCoalescedText(input),
    topicEnded,
    taskKind
  };
}

function normalizeDirectGateDecision(decision: z.infer<typeof structuredGateDecisionSchema>): GateDecision {
  const topicEnded =
    decision.topicEnded ?? (decision.replyDecision === "reply" && decision.memoryDecision !== "defer");

  return {
    ...decision,
    topicEnded,
    taskKind: decision.inputKind === "task" ? decision.taskKind ?? "result_output" : undefined
  };
}

function normalizeInputKind(providerDecision: z.infer<typeof providerGateDecisionSchema>): InputKind {
  if (providerDecision.isTaskControl) {
    return "task_control";
  }

  return providerDecision.isTask ? "task" : "chat";
}

function normalizeTaskKind(
  inputKind: InputKind,
  providerDecision: z.infer<typeof providerGateDecisionSchema>
): TaskKind | undefined {
  if (inputKind !== "task") {
    return undefined;
  }

  if (providerDecision.taskKind) {
    return providerDecision.taskKind;
  }

  if (providerDecision.isTimeReminderTask) {
    return "time_reminder";
  }

  if (providerDecision.isResultOutputTask) {
    return "result_output";
  }

  return "result_output";
}

function normalizeReplyDecision(input: {
  inputKind: InputKind;
  shouldReply?: boolean;
  topicEnded?: boolean;
}): ReplyDecision {
  const shouldReply = input.shouldReply ?? (input.inputKind !== "chat");

  return shouldReply ? "reply" : "wait";
}

function normalizeMemoryDecision(input: {
  inputKind: InputKind;
  replyDecision: ReplyDecision;
  shouldWrite?: boolean;
}): MemoryDecision {
  if (input.inputKind === "task" || input.inputKind === "task_control") {
    return "skip";
  }

  if (input.replyDecision === "wait") {
    return "defer";
  }

  return input.shouldWrite ? "write_now" : "skip";
}

function inferTopicEnded(inputKind: InputKind, shouldReply?: boolean): boolean {
  if (inputKind === "task" || inputKind === "task_control") {
    return true;
  }

  return shouldReply === true;
}

function estimateNormalizedConfidence(input: {
  inputKind: InputKind;
  replyDecision: ReplyDecision;
  memoryDecision: MemoryDecision;
}): number {
  if (input.inputKind === "task" || input.inputKind === "task_control") {
    return 0.82;
  }

  if (input.replyDecision === "wait") {
    return 0.7;
  }

  return input.memoryDecision === "write_now" ? 0.76 : 0.72;
}

function buildCoalescedText(input: GateEvaluationInput): string {
  const recentUserMessages =
    input.recentUserMessages.length > 0
      ? input.recentUserMessages
      : (input.recentMessages ?? [])
          .filter((message) => message.role === "user")
          .map((message) => message.content);

  return [...recentUserMessages, input.currentMessage].map((message) => message.trim()).filter(Boolean).join("\n");
}
