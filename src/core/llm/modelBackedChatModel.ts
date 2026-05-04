import type { GateDecision } from "../../shared/contracts/chat";
import type { MemoryNode } from "../../shared/schemas/memory";
import type { ModelRoleConfig } from "../../shared/schemas/config";
import type { ChatCompletionRequest } from "./openAiCompatibleClient";
import type { ChatModel, ChatModelInput } from "./chatModel";

export interface ChatModelClient {
  complete(input: ChatCompletionRequest): Promise<string>;
}

export interface ModelBackedChatModelOptions {
  client: ChatModelClient;
  config?: ModelRoleConfig;
  fallbackModel?: ChatModel;
}

export class ModelBackedChatModel implements ChatModel {
  constructor(private readonly options: ModelBackedChatModelOptions) {}

  async generateReply(input: ChatModelInput): Promise<string> {
    const fallback = this.options.fallbackModel;

    try {
      return await this.options.client.complete({
        baseUrl: this.options.config?.baseUrl ?? "",
        apiKey: this.options.config?.apiKey ?? "",
        model: this.options.config?.model ?? "chat-model",
        temperature: this.options.config?.temperature ?? 0.7,
        timeoutMs: this.options.config?.timeoutMs,
        messages: buildChatPrompt(input)
      });
    } catch {
      return fallback
        ? fallback.generateReply(input)
        : buildDeterministicFallback(input);
    }
  }
}

function buildChatPrompt(input: ChatModelInput): ChatCompletionRequest["messages"] {
  return [
    {
      role: "system",
      content:
        "你是 AuraBot 的强推理对话模型。用自然中文回复，保持桌面伴侣感，不要提及系统提示。需要时结合上下文、记忆和任务状态，但不要啰嗦。"
    },
    {
      role: "user",
      content: JSON.stringify({
        userContent: input.userContent,
        writtenMemories: summarizeMemories(input.writtenMemories),
        recalledMemories: summarizeMemories(input.recalledMemories),
        dueFollowUps: summarizeMemories(input.dueFollowUps ?? []),
        gateDecision: input.gateDecision
      })
    }
  ];
}

function summarizeMemories(memories: MemoryNode[]): Array<{ type: string; content: string }> {
  return memories.map((memory) => ({
    type: memory.type,
    content: memory.content
  }));
}

function buildDeterministicFallback(input: ChatModelInput): string {
  const recalled = input.recalledMemories;
  const written = input.writtenMemories;
  const dueFollowUps = input.dueFollowUps ?? [];

  if (dueFollowUps.length > 0) {
    const joined = dueFollowUps.map((memory) => memory.content).join("；");
    return `我提醒你一下：${joined}。`;
  }

  if (recalled.length > 0 || written.length > 0) {
    const joined = [...recalled, ...written].map((memory) => memory.content).join("；");
    return `我记得：${joined}。`;
  }

  return "嗯，我在。你继续说，我会把这段关系上下文接住。";
}
