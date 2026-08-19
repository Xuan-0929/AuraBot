/**
 * 将结构化 Gate 模型输出规范为当前 chat/task 公共契约。
 *
 * 模型只判断回复、最小 Task 意图、Memory 与话题边界；旧 task_control、TaskKind
 * 和 reminder subtype 不再进入 schema 或后续执行逻辑。
 */

import { z } from "zod";
import type {
  GateDecision,
  InputKind,
  MemoryDecision,
  ReplyDecision
} from "@shared/contracts/chat";
import type { ModelRoleConfig } from "@shared/schemas/config";
import type { GateEvaluationInput } from "../orchestration/gateEvaluator";
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
  inputKind: z.enum(["chat", "task"]),
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
    memory: z.object({ shouldWrite: z.boolean().optional() }).optional(),
    isTopicEnded: z.boolean().optional(),
    confidence: z.number().min(0).max(1).optional(),
    reason: z.string().min(1).optional()
  })
  .passthrough()
  .refine(
    (value) =>
      value.shouldReply !== undefined ||
      value.isTask !== undefined ||
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
        "字段必须是 replyDecision(reply|wait), inputKind(chat|task), memoryDecision(write_now|defer|skip), confidence(0-1), reason, coalescedText, topicEnded。",
        "recentMessages 包含最近 user/assistant 上下文，请判断用户是在继续聊天还是提出任何 Task 创建、提醒、查询或控制请求。",
        "所有 Task 请求都判为 inputKind=task、replyDecision=reply、memoryDecision=skip、topicEnded=true；当前执行层会统一回复暂不可用。",
        "普通聊天是否回复应结合上下文；短句、无标点或 topicEnded=false 不能单独作为等待理由。"
      ].join("\n")
    },
    { role: "user", content: JSON.stringify(input) }
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

function normalizeGateDecision(raw: unknown, input: GateEvaluationInput): GateDecision {
  const direct = structuredGateDecisionSchema.safeParse(raw);
  if (direct.success) {
    if (direct.data.inputKind === "task") {
      return {
        ...direct.data,
        replyDecision: "reply",
        memoryDecision: "skip",
        topicEnded: true
      };
    }
    return {
      ...direct.data,
      topicEnded:
        direct.data.topicEnded ??
        (direct.data.replyDecision === "reply" && direct.data.memoryDecision !== "defer")
    };
  }

  const provider = providerGateDecisionSchema.parse(raw);
  const inputKind: InputKind = provider.isTask ? "task" : "chat";
  const replyDecision: ReplyDecision =
    inputKind === "task" || provider.shouldReply === true ? "reply" : "wait";
  const memoryDecision: MemoryDecision =
    inputKind === "task"
      ? "skip"
      : replyDecision === "wait"
        ? "defer"
        : provider.memory?.shouldWrite
          ? "write_now"
          : "skip";

  return {
    replyDecision,
    inputKind,
    memoryDecision,
    confidence: provider.confidence ?? (inputKind === "task" ? 0.82 : 0.72),
    reason: provider.reason ?? "model-gate: normalized provider schema",
    coalescedText: buildCoalescedText(input),
    topicEnded: provider.isTopicEnded ?? (inputKind === "task" || replyDecision === "reply")
  };
}

function buildCoalescedText(input: GateEvaluationInput): string {
  const recent =
    input.recentUserMessages.length > 0
      ? input.recentUserMessages
      : (input.recentMessages ?? [])
          .filter((message) => message.role === "user")
          .map((message) => message.content);
  return [...recent, input.currentMessage]
    .map((message) => message.trim())
    .filter(Boolean)
    .join("\n");
}
