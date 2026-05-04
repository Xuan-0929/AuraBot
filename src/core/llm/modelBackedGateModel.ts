import { z } from "zod";
import type { GateDecision } from "../../shared/contracts/chat";
import type { GateEvaluationInput } from "../orchestration/gateEvaluator";
import { evaluateGate } from "../orchestration/gateEvaluator";
import type { ModelRoleConfig } from "../../shared/schemas/config";
import type { ChatCompletionRequest } from "./openAiCompatibleClient";

export interface GateModelClient {
  complete(input: ChatCompletionRequest): Promise<string>;
}

export interface ModelBackedGateModelOptions {
  client: GateModelClient;
  config?: ModelRoleConfig;
  fallbackModel?: { evaluate(input: GateEvaluationInput): Promise<GateDecision> };
}

const gateDecisionSchema = z.object({
  replyDecision: z.enum(["reply", "wait"]),
  inputKind: z.enum(["chat", "task", "task_control"]),
  memoryDecision: z.enum(["write_now", "defer", "skip"]),
  confidence: z.number().min(0).max(1),
  reason: z.string().min(1),
  coalescedText: z.string().min(1),
  topicEnded: z.boolean().optional()
});

export class ModelBackedGateModel {
  constructor(private readonly options: ModelBackedGateModelOptions) {}

  async evaluate(input: GateEvaluationInput): Promise<GateDecision> {
    const fallback = this.options.fallbackModel ?? { evaluate: async (value: GateEvaluationInput) => evaluateGate(value) };

    try {
      const content = await this.options.client.complete({
        baseUrl: this.options.config?.baseUrl ?? "",
        apiKey: this.options.config?.apiKey ?? "",
        model: this.options.config?.model ?? "gate-model",
        temperature: this.options.config?.temperature ?? 0,
        timeoutMs: this.options.config?.timeoutMs,
        messages: buildGatePrompt(input)
      });
      const decision = gateDecisionSchema.parse(parseJsonFromModel(content));

      return {
        ...decision,
        topicEnded:
          decision.topicEnded ??
          (decision.replyDecision === "reply" && decision.memoryDecision !== "defer")
      };
    } catch (error) {
      return fallback.evaluate(input);
    }
  }
}

function buildGatePrompt(input: GateEvaluationInput): ChatCompletionRequest["messages"] {
  return [
    {
      role: "system",
      content:
        "你是 AuraBot 的轻量判断模型。只返回 JSON，不要解释。判断用户输入是否该回复、是否是任务、是否是任务控制、记忆决策、话题是否结束。"
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
