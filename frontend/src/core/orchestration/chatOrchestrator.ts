/**
 * 编排 Electron MVP 的普通主聊天、Gate、Memory 与模型恢复流程。
 *
 * 每次输入只写主 session；chat 意图执行原有 Memory/streaming 回复，最小 task 意图
 * 写入固定不可用 assistant 消息，不创建 Task 数据、child session 或执行进程。
 */

import { randomUUID } from "node:crypto";
import type { SendChatMessageInput, SendChatMessageResult } from "@shared/contracts/chat";
import type { Message } from "@shared/schemas/message";
import type { MemoryNode } from "@shared/schemas/memory";
import type { ChatModel } from "../llm/chatModel";
import type { GateModel } from "../llm/gateModel";
import {
  ModelConfigurationError,
  ModelInvocationError,
  summarizeModelError,
  type ModelRoleName
} from "../llm/modelErrors";
import { shouldAskMemoryRecall, type MemoryExtractor } from "../memory/memoryExtractor";
import type { MemoryService } from "../memory/memoryService";
import type { ProfileService } from "../profiles/profileService";
import type { MessageRepository } from "../storage/repositories/messageRepository";
import type { SessionRepository } from "../storage/repositories/sessionRepository";
import type { EventLogService } from "../telemetry/eventLogService";
import type { VoiceAdapter } from "../voice/voiceAdapter";

const GATE_CONTEXT_MESSAGE_LIMIT = 10;
export const TASK_UNAVAILABLE_MESSAGE = "Task 功能正在迁移，当前暂不可用。";

export interface ChatOrchestratorDependencies {
  profileService: ProfileService;
  sessionRepository: SessionRepository;
  messageRepository: MessageRepository;
  memoryExtractor: MemoryExtractor;
  memoryService: MemoryService;
  eventLogService: EventLogService;
  gateModel: GateModel;
  chatModel: ChatModel;
  voiceAdapter: VoiceAdapter;
}

export interface SendChatMessageOptions {
  onAssistantMessageCreated?(message: Message): void;
  onAssistantMessageUpdated?(message: Message): void;
}

export class ChatOrchestrator {
  constructor(private readonly dependencies: ChatOrchestratorDependencies) {}

  updateModelDependencies(
    dependencies: Partial<
      Pick<
        ChatOrchestratorDependencies,
        "gateModel" | "chatModel" | "voiceAdapter" | "memoryExtractor" | "memoryService"
      >
    >
  ): void {
    Object.assign(this.dependencies, dependencies);
  }

  async sendMessage(
    input: SendChatMessageInput,
    options: SendChatMessageOptions = {}
  ): Promise<SendChatMessageResult> {
    const profile = this.dependencies.profileService.getOrSeedActiveProfile();
    const mainSession = this.dependencies.sessionRepository.getOrCreateMainSession(profile.id);
    const userMessage = this.dependencies.messageRepository.create({
      sessionId: mainSession.id,
      profileId: profile.id,
      role: "user",
      content: input.content,
      attachments: (input.attachments ?? []).map((attachment) => ({
        id: `message_attachment_${randomUUID()}`,
        kind: attachment.kind,
        purpose: "input",
        name: attachment.name
      }))
    });
    const recentMessages = this.dependencies.messageRepository
      .listRecentBySession(mainSession.id, GATE_CONTEXT_MESSAGE_LIMIT + 1)
      .filter((message) => message.id !== userMessage.id)
      .slice(-GATE_CONTEXT_MESSAGE_LIMIT)
      .map((message) => ({ role: message.role, content: message.content }));
    const recentUserMessages = recentMessages
      .filter((message) => message.role === "user")
      .map((message) => message.content);
    let assistantMessage: Message | undefined;
    let gateDecision: SendChatMessageResult["gateDecision"];

    try {
      gateDecision = await this.dependencies.gateModel.evaluate({
        currentMessage: input.content,
        recentUserMessages,
        recentMessages
      });

      this.dependencies.eventLogService.record("gate.decision", {
        profileId: profile.id,
        sessionId: mainSession.id,
        replyDecision: gateDecision.replyDecision,
        inputKind: gateDecision.inputKind,
        memoryDecision: gateDecision.memoryDecision,
        confidence: gateDecision.confidence,
        reason: gateDecision.reason,
        topicEnded: gateDecision.topicEnded
      });

      if (gateDecision.replyDecision === "wait") {
        this.recordPersistedMessage({
          profileId: profile.id,
          sessionId: mainSession.id,
          userMessageId: userMessage.id,
          assistantMessageId: null,
          gateDecision: "wait",
          writtenMemoryIds: []
        });
        return { userMessage, agentStatus: "listening", gateDecision };
      }

      if (gateDecision.inputKind === "task") {
        assistantMessage = this.dependencies.messageRepository.create({
          sessionId: mainSession.id,
          profileId: profile.id,
          role: "assistant",
          content: TASK_UNAVAILABLE_MESSAGE
        });
        this.recordPersistedMessage({
          profileId: profile.id,
          sessionId: mainSession.id,
          userMessageId: userMessage.id,
          assistantMessageId: assistantMessage.id,
          gateDecision: "reply",
          writtenMemoryIds: []
        });
        return {
          userMessage,
          assistantMessage,
          agentStatus: "idle",
          gateDecision
        };
      }

      const semanticSegment = gateDecision.coalescedText;
      const writtenMemoryIds =
        gateDecision.memoryDecision === "write_now"
          ? await this.writeMemoriesFromMessage({
              profileId: profile.id,
              sessionId: mainSession.id,
              content: semanticSegment
            })
          : [];
      const memoryContext = await this.dependencies.memoryService.retrieveRelevant(
        profile.id,
        semanticSegment
      );
      const dueFollowUps = this.dependencies.memoryService.retrieveDueFollowUps(
        profile.id,
        new Date().toISOString()
      );
      const writtenMemories = memoryContext.nodes.filter((node) =>
        writtenMemoryIds.includes(node.id)
      );
      const recalledMemories = dedupeMemoryNodes([
        ...dueFollowUps,
        ...memoryContext.nodes.filter((node) => !writtenMemoryIds.includes(node.id))
      ]);

      if (memoryContext.nodes.length > 0 && shouldAskMemoryRecall(semanticSegment)) {
        this.dependencies.eventLogService.record("memory.recall_used", {
          profileId: profile.id,
          sessionId: mainSession.id,
          nodeIds: memoryContext.nodes.map((node) => node.id),
          summaryTopics: memoryContext.summaries.map((summary) => summary.topic)
        });
      }

      assistantMessage = this.dependencies.messageRepository.create({
        sessionId: mainSession.id,
        profileId: profile.id,
        role: "assistant",
        content: ""
      });
      options.onAssistantMessageCreated?.(assistantMessage);

      let streamedContent = "";
      const finalReply = await this.dependencies.chatModel.generateReply(
        {
          userContent: semanticSegment,
          soul: profile.soul,
          writtenMemories,
          recalledMemories: shouldAskMemoryRecall(semanticSegment)
            ? memoryContext.nodes
            : recalledMemories,
          dueFollowUps,
          gateDecision
        },
        {
          onDelta: (delta) => {
            if (!delta) {
              return;
            }
            streamedContent += delta;
            const updated = this.dependencies.messageRepository.updateContent({
              messageId: assistantMessage!.id,
              content: streamedContent
            });
            if (updated) {
              assistantMessage = updated;
              options.onAssistantMessageUpdated?.(updated);
            }
          }
        }
      );

      if (finalReply !== assistantMessage.content) {
        const updated = this.dependencies.messageRepository.updateContent({
          messageId: assistantMessage.id,
          content: finalReply
        });
        if (updated) {
          assistantMessage = updated;
          options.onAssistantMessageUpdated?.(updated);
        }
      }

      if (dueFollowUps.length > 0) {
        this.dependencies.memoryService.completeFollowUps(
          profile.id,
          dueFollowUps.map((memory) => memory.id)
        );
        this.dependencies.eventLogService.record("memory.follow_up_triggered", {
          profileId: profile.id,
          sessionId: mainSession.id,
          nodeIds: dueFollowUps.map((memory) => memory.id)
        });
      }

      this.recordPersistedMessage({
        profileId: profile.id,
        sessionId: mainSession.id,
        userMessageId: userMessage.id,
        assistantMessageId: assistantMessage.id,
        gateDecision: "reply",
        writtenMemoryIds
      });

      return {
        userMessage,
        assistantMessage,
        agentStatus: "idle",
        gateDecision
      };
    } catch (error) {
      return this.handleModelFailure({
        profileId: profile.id,
        sessionId: mainSession.id,
        userMessage,
        gateDecision,
        assistantMessage,
        error
      });
    }
  }

  private async writeMemoriesFromMessage(input: {
    profileId: string;
    sessionId: string;
    content: string;
  }): Promise<string[]> {
    const candidates = await this.dependencies.memoryExtractor.extract({
      message: input.content,
      nowIso: new Date().toISOString(),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
    });
    const writtenIds: string[] = [];
    let mostRecentEventId: string | undefined;

    for (const candidate of candidates) {
      const edges =
        candidate.relationToPrevious && mostRecentEventId
          ? [{ ...candidate.relationToPrevious, targetNodeId: mostRecentEventId }]
          : [];
      const nodeId = await this.dependencies.memoryService.enqueueWrite({
        profileId: input.profileId,
        sourceSessionId: input.sessionId,
        node: candidate.node,
        edges
      });
      writtenIds.push(nodeId);
      if (candidate.node.type === "event") {
        mostRecentEventId = nodeId;
      }
      this.dependencies.eventLogService.record("memory.write_completed", {
        profileId: input.profileId,
        sessionId: input.sessionId,
        nodeId,
        nodeType: candidate.node.type,
        topic: candidate.topic
      });
    }

    return writtenIds;
  }

  private recordPersistedMessage(input: {
    profileId: string;
    sessionId: string;
    userMessageId: string;
    assistantMessageId: string | null;
    writtenMemoryIds: string[];
    gateDecision: "reply" | "wait";
  }): void {
    this.dependencies.eventLogService.record("chat.message_persisted", input);
  }

  private handleModelFailure(input: {
    profileId: string;
    sessionId: string;
    userMessage: Message;
    gateDecision?: SendChatMessageResult["gateDecision"];
    assistantMessage?: Message;
    error: unknown;
  }): SendChatMessageResult {
    const content = buildModelRecoveryMessage(input.error);
    const assistantMessage = input.assistantMessage
      ? this.dependencies.messageRepository.updateContent({
          messageId: input.assistantMessage.id,
          content
        }) ?? input.assistantMessage
      : this.dependencies.messageRepository.create({
          sessionId: input.sessionId,
          profileId: input.profileId,
          role: "assistant",
          content
        });
    const summary = summarizeModelError(input.error);

    this.dependencies.eventLogService.record("chat.model_recovery_emitted", {
      profileId: input.profileId,
      sessionId: input.sessionId,
      userMessageId: input.userMessage.id,
      assistantMessageId: assistantMessage.id,
      modelRole: getModelRoleFromError(input.error),
      errorCode: summary.code
    });
    this.recordPersistedMessage({
      profileId: input.profileId,
      sessionId: input.sessionId,
      userMessageId: input.userMessage.id,
      assistantMessageId: assistantMessage.id,
      writtenMemoryIds: [],
      gateDecision: input.gateDecision?.replyDecision ?? "reply"
    });

    return {
      userMessage: input.userMessage,
      assistantMessage,
      agentStatus: "idle",
      gateDecision: input.gateDecision
    };
  }
}

function dedupeMemoryNodes(memories: MemoryNode[]): MemoryNode[] {
  const seen = new Set<string>();
  return memories.filter((memory) => {
    if (seen.has(memory.id)) {
      return false;
    }
    seen.add(memory.id);
    return true;
  });
}

function buildModelRecoveryMessage(error: unknown): string {
  const summary = summarizeModelError(error);
  const role = getModelRoleFromError(error);

  if (role === "memoryModel") {
    return "我这边记忆模型现在没连好，这句我先不敢乱记。你可以先继续聊，或者去设置里测一下记忆模型连接。";
  }
  if (role === "gateModel") {
    return summary.code === "authentication_failed"
      ? "我这边判断模型没连好，这句没法稳稳接住。你去设置里检查一下判断模型的 API Key，再测一下连接，然后把这句话重新发我一次。"
      : "我这边判断模型刚刚没连上，这句没能稳稳接住。你去设置里测试一下判断模型连接，连好后再发我一次。";
  }
  if (summary.code === "authentication_failed") {
    return "我这边聊天模型现在没连好，API Key 这边像是有点问题。你去设置里检查一下聊天模型配置，再测一下连接，然后我就能继续接着回你。";
  }
  if (summary.code === "missing_configuration" || summary.code === "unsupported_provider") {
    return "我这边聊天模型还没接好，先没法正常回你。你去设置里看一下聊天模型配置，再测试一下连接，连上之后再发我一次。";
  }
  return "我这边聊天模型刚刚没连上，这句先没法好好接住。你去设置里测一下聊天模型连接，连好后我继续。";
}

function getModelRoleFromError(error: unknown): ModelRoleName | undefined {
  return error instanceof ModelConfigurationError || error instanceof ModelInvocationError
    ? error.role
    : undefined;
}
