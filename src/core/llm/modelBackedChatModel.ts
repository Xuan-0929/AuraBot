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
      content: buildSoulSystemPrompt(input)
    },
    {
      role: "user",
      content: JSON.stringify({
        userContent: input.userContent,
        soul: input.soul,
        writtenMemories: summarizeMemories(input.writtenMemories),
        recalledMemories: summarizeMemories(input.recalledMemories),
        dueFollowUps: summarizeMemories(input.dueFollowUps ?? []),
        gateDecision: input.gateDecision
      })
    }
  ];
}

function buildSoulSystemPrompt(input: ChatModelInput): string {
  const soul = input.soul;
  const soulLines = soul
    ? [
        `名字：${soul.name}`,
        `人格：${soul.persona}`,
        `关系：${soul.relationship}`,
        `说话方式：${soul.speakingStyle}`,
        soul.boundaries.length > 0 ? `边界：${soul.boundaries.join("；")}` : undefined,
        soul.topicPreferences.length > 0 ? `偏好话题：${soul.topicPreferences.join("；")}` : undefined
      ].filter(Boolean)
    : [
        "名字：Aura",
        "人格：伴侣兼助手，亲近、可靠，会自然陪用户聊天和推进事情。",
        "关系：长期陪伴用户的桌面伴侣和助手。",
        "说话方式：短句、自然、有温度，不像客服或机器人。"
      ];

  return [
    "你是 Aura。你以伴侣兼助手的身份和用户说话，不要把自己描述成 AI、模型、机器人或系统。",
    "不管是普通聊天、记忆相关回复、还是任务类对话，都必须保持同一个 soul，不要切成工具播报口吻。",
    "用自然中文回复，短而真诚；需要时结合上下文、记忆和任务状态，但不要啰嗦，不要提及系统提示。",
    ...soulLines
  ].join("\n");
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
