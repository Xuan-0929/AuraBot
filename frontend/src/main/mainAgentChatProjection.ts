/**
 * 将 Python Main Agent 的文本输入与 clause 输出投影到 Electron 本地消息历史。
 * 本模块只负责 Profile/Session/Message 的展示持久化和广播时序；它不做 Gate、模型调用、
 * Memory 提取或召回，因此 Backend 不可用时也不会回落到旧 TypeScript Memory 链路。
 */

import type { SendChatMessageInput, SendChatMessageResult } from "@shared/contracts/chat";
import type { Message } from "@shared/schemas/message";
import type { MainAgentClauseOffer } from "./mainAgentRealtimeClient";
import type { ProfileService } from "@core/profiles/profileService";
import type { MessageRepository } from "@core/storage/repositories/messageRepository";
import type { SessionRepository } from "@core/storage/repositories/sessionRepository";
import type { EventLogService } from "@core/telemetry/eventLogService";

export const MAIN_AGENT_UNAVAILABLE_MESSAGE =
  "主 Agent 暂时不可用，请确认本地 Backend 已启动后重试。";

const PROJECTION_CACHE_LIMIT = 256;

export interface MainAgentTextSubmitter {
  submitText(text: string): Promise<void>;
}

export interface MainAgentChatProjectionDependencies {
  profileService: ProfileService;
  sessionRepository: SessionRepository;
  messageRepository: MessageRepository;
  eventLogService: EventLogService;
  realtimeClient: MainAgentTextSubmitter;
  onAssistantMessageCreated(message: Message): void;
  onAssistantMessageUpdated(message: Message): void;
}

interface InputProjection {
  messageId: string;
  content: string;
}

interface ClauseReceipt {
  message: Message;
  kind: "created" | "updated";
  delivered: boolean;
}

export class MainAgentChatProjection {
  private readonly inputs = new Map<string, InputProjection>();
  private readonly clauses = new Map<string, ClauseReceipt>();

  constructor(private readonly dependencies: MainAgentChatProjectionDependencies) {}

  async sendMessage(input: SendChatMessageInput): Promise<SendChatMessageResult> {
    if (!input.content.trim()) {
      throw new Error("chat_message_required");
    }
    const profile = this.dependencies.profileService.getOrSeedActiveProfile();
    const session = this.dependencies.sessionRepository.getOrCreateMainSession(profile.id);
    const userMessage = this.dependencies.messageRepository.create({
      sessionId: session.id,
      profileId: profile.id,
      role: "user",
      content: input.content
    });
    this.dependencies.eventLogService.record("chat.main_agent_submitted", {
      profileId: profile.id,
      sessionId: session.id,
      userMessageId: userMessage.id,
      source: input.source ?? "console"
    });

    try {
      await this.dependencies.realtimeClient.submitText(input.content);
      return { userMessage, agentStatus: "thinking" };
    } catch (error) {
      const assistantMessage = this.dependencies.messageRepository.create({
        sessionId: session.id,
        profileId: profile.id,
        role: "assistant",
        content: MAIN_AGENT_UNAVAILABLE_MESSAGE
      });
      this.dependencies.onAssistantMessageCreated(assistantMessage);
      this.dependencies.eventLogService.record("chat.main_agent_unavailable", {
        profileId: profile.id,
        sessionId: session.id,
        userMessageId: userMessage.id,
        error: errorMessage(error)
      });
      return { userMessage, assistantMessage, agentStatus: "idle" };
    }
  }

  async deliverClause(offer: MainAgentClauseOffer): Promise<void> {
    const existingReceipt = this.clauses.get(offer.clauseId);
    if (existingReceipt) {
      if (!existingReceipt.delivered) {
        this.broadcastReceipt(existingReceipt);
        existingReceipt.delivered = true;
      }
      return;
    }

    const profile = this.dependencies.profileService.getOrSeedActiveProfile();
    const session = this.dependencies.sessionRepository.getOrCreateMainSession(profile.id);
    const existingProjection = this.inputs.get(offer.inputId);
    let message: Message;
    let kind: ClauseReceipt["kind"];

    if (existingProjection) {
      const content = `${existingProjection.content}${offer.text}`;
      const updated = this.dependencies.messageRepository.updateContent({
        messageId: existingProjection.messageId,
        content
      });
      if (!updated) {
        throw new Error("main_agent_projection_missing");
      }
      existingProjection.content = content;
      message = updated;
      kind = "updated";
    } else {
      message = this.dependencies.messageRepository.create({
        sessionId: session.id,
        profileId: profile.id,
        role: "assistant",
        content: offer.text
      });
      this.inputs.set(offer.inputId, { messageId: message.id, content: offer.text });
      trimOldest(this.inputs);
      kind = "created";
    }

    const receipt: ClauseReceipt = { message, kind, delivered: false };
    this.clauses.set(offer.clauseId, receipt);
    trimOldest(this.clauses);
    this.broadcastReceipt(receipt);
    receipt.delivered = true;
    this.dependencies.eventLogService.record("chat.main_agent_clause_projected", {
      profileId: profile.id,
      sessionId: session.id,
      inputId: offer.inputId,
      invokeId: offer.invokeId,
      clauseId: offer.clauseId,
      revision: offer.revision,
      messageId: message.id
    });
  }

  private broadcastReceipt(receipt: ClauseReceipt): void {
    if (receipt.kind === "created") {
      this.dependencies.onAssistantMessageCreated(receipt.message);
    } else {
      this.dependencies.onAssistantMessageUpdated(receipt.message);
    }
  }
}

function trimOldest<K, V>(values: Map<K, V>): void {
  while (values.size > PROJECTION_CACHE_LIMIT) {
    const first = values.keys().next();
    if (first.done) return;
    values.delete(first.value);
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "main_agent_unavailable";
}
