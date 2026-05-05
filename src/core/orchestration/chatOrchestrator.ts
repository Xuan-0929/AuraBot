import type { SendChatMessageInput, SendChatMessageResult } from "../../shared/contracts/chat";
import type { Message } from "../../shared/schemas/message";
import type { MemoryNode } from "../../shared/schemas/memory";
import type { Soul } from "../../shared/schemas/profile";
import { shouldAskMemoryRecall, type MemoryExtractor } from "../memory/memoryExtractor";
import type { MemoryService } from "../memory/memoryService";
import type { MessageRepository } from "../storage/repositories/messageRepository";
import type { SessionRepository } from "../storage/repositories/sessionRepository";
import type { TaskService } from "../tasks/taskService";
import type { ProfileService } from "../profiles/profileService";
import type { EventLogService } from "../telemetry/eventLogService";
import { parseTaskControlRequest } from "../tasks/taskControlRouter";
import type { GateModel } from "../llm/gateModel";
import type { ChatModel } from "../llm/chatModel";
import type { VoiceAdapter } from "../voice/voiceAdapter";
import {
  buildTaskSettledMessageDraftWithVoice,
  buildTaskStatusResponseWithVoice
} from "../tasks/taskPresentation";

const GATE_CONTEXT_MESSAGE_LIMIT = 10;

export interface ChatOrchestratorDependencies {
  profileService: ProfileService;
  sessionRepository: SessionRepository;
  messageRepository: MessageRepository;
  memoryExtractor: MemoryExtractor;
  memoryService: MemoryService;
  taskService: TaskService;
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

  async sendMessage(input: SendChatMessageInput, options: SendChatMessageOptions = {}): Promise<SendChatMessageResult> {
    const profile = this.dependencies.profileService.getOrSeedActiveProfile();
    const mainSession = this.dependencies.sessionRepository.getOrCreateMainSession(profile.id);

    const userMessage = this.dependencies.messageRepository.create({
      sessionId: mainSession.id,
      profileId: profile.id,
      role: "user",
      content: input.content
    });
    const recentMessages = this.dependencies.messageRepository
      .listRecentBySession(mainSession.id, GATE_CONTEXT_MESSAGE_LIMIT + 1)
      .filter((message) => message.id !== userMessage.id)
      .slice(-GATE_CONTEXT_MESSAGE_LIMIT)
      .map((message) => ({
        role: message.role,
        content: message.content
      }));
    const recentUserMessages = recentMessages
      .filter((message) => message.role === "user")
      .map((message) => message.content);
    const gateDecision = await this.dependencies.gateModel.evaluate({
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
      this.dependencies.eventLogService.record("chat.message_persisted", {
        profileId: profile.id,
        sessionId: mainSession.id,
        userMessageId: userMessage.id,
        assistantMessageId: null,
        writtenMemoryIds: [],
        gateDecision: gateDecision.replyDecision
      });

      return {
        userMessage,
        agentStatus: "listening",
        gateDecision
      };
    }

    const semanticSegment = gateDecision.inputKind === "chat" ? gateDecision.coalescedText : input.content.trim();
    if (gateDecision.inputKind === "task") {
      const createdTask = this.dependencies.taskService.createTaskFromChat({
        profileId: profile.id,
        parentSessionId: mainSession.id,
        title: semanticSegment
      });
      const assistantMessage = this.dependencies.messageRepository.create({
        sessionId: mainSession.id,
        profileId: profile.id,
        role: "assistant",
        content: await this.dependencies.voiceAdapter.render({
          scene: "task.accepted",
          channel: "chat",
          soul: profile.soul,
          facts: {
            taskTitle: semanticSegment
          },
          fallbackText: "好，我开始弄。"
        })
      });

      this.dependencies.eventLogService.record("chat.message_persisted", {
        profileId: profile.id,
        sessionId: mainSession.id,
        userMessageId: userMessage.id,
        assistantMessageId: assistantMessage.id,
        writtenMemoryIds: [],
        createdTaskId: createdTask.id,
        gateDecision: gateDecision.replyDecision
      });

      return {
        userMessage,
        assistantMessage,
        agentStatus: "tasking",
        gateDecision,
        createdTask
      };
    }

    if (gateDecision.inputKind === "task_control") {
      const taskControlResult = await this.handleTaskControl({
        profileId: profile.id,
        sessionId: mainSession.id,
        userMessageId: userMessage.id,
        content: input.content,
        soul: profile.soul
      });

      return {
        userMessage,
        assistantMessage: taskControlResult.assistantMessage,
        agentStatus: taskControlResult.updatedTask ? "tasking" : "idle",
        gateDecision,
        updatedTask: taskControlResult.updatedTask
      };
    }

    const writtenMemoryIds =
      gateDecision.memoryDecision === "write_now"
        ? await this.writeMemoriesFromMessage({
            profileId: profile.id,
            sessionId: mainSession.id,
            content: semanticSegment
          })
        : [];
    const memoryContext = await this.dependencies.memoryService.retrieveRelevant(profile.id, semanticSegment);
    const dueFollowUps = this.dependencies.memoryService.retrieveDueFollowUps(
      profile.id,
      new Date().toISOString()
    );
    const writtenMemories = memoryContext.nodes.filter((node) => writtenMemoryIds.includes(node.id));
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

    let assistantMessage = this.dependencies.messageRepository.create({
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
        recalledMemories: shouldAskMemoryRecall(semanticSegment) ? memoryContext.nodes : recalledMemories,
        dueFollowUps,
        gateDecision
      },
      {
        onDelta: (delta) => {
          if (!delta) {
            return;
          }

          streamedContent += delta;
          const updatedMessage = this.dependencies.messageRepository.updateContent({
            messageId: assistantMessage.id,
            content: streamedContent
          });

          if (updatedMessage) {
            assistantMessage = updatedMessage;
            options.onAssistantMessageUpdated?.(assistantMessage);
          }
        }
      }
    );

    if (finalReply !== assistantMessage.content) {
      const updatedMessage = this.dependencies.messageRepository.updateContent({
        messageId: assistantMessage.id,
        content: finalReply
      });

      if (updatedMessage) {
        assistantMessage = updatedMessage;
        options.onAssistantMessageUpdated?.(assistantMessage);
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

    this.dependencies.eventLogService.record("chat.message_persisted", {
      profileId: profile.id,
      sessionId: mainSession.id,
      userMessageId: userMessage.id,
      assistantMessageId: assistantMessage.id,
      writtenMemoryIds,
      gateDecision: gateDecision.replyDecision
    });

    return {
      userMessage,
      assistantMessage,
      agentStatus: "idle",
      gateDecision
    };
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

  private async handleTaskControl(input: {
    profileId: string;
    sessionId: string;
    userMessageId: string;
    content: string;
    soul?: Soul;
  }) {
    const request = parseTaskControlRequest(input.content);
    const selectedTask = this.dependencies.taskService.findTaskForChatControl({
      profileId: input.profileId,
      targetHint: request.targetHint,
      activeOnly: request.action === "interrupt"
    });
    let updatedTask;
    let responseContent: string;

    if (!selectedTask) {
      responseContent = request.targetHint
        ? await this.dependencies.voiceAdapter.render({
            scene: "task.control_not_found",
            channel: "chat",
            soul: input.soul,
            facts: {
              targetHint: request.targetHint
            },
            fallbackText: `我没有找到和「${request.targetHint}」匹配的任务。`
          })
        : await this.dependencies.voiceAdapter.render({
            scene: "task.control_not_found",
            channel: "chat",
            soul: input.soul,
            facts: {},
            fallbackText: "我这边还没有正在跟的任务。"
          });
    } else if (request.action === "query") {
      responseContent = await buildTaskStatusResponseWithVoice(
        selectedTask,
        this.dependencies.taskService.listTaskEvents(selectedTask.id),
        {
          soul: input.soul,
          voiceAdapter: this.dependencies.voiceAdapter
        }
      );
    } else if (request.action === "start") {
      if (selectedTask.status !== "queued") {
        updatedTask = selectedTask;
        responseContent = await buildTaskStatusResponseWithVoice(
          selectedTask,
          this.dependencies.taskService.listTaskEvents(selectedTask.id),
          {
            soul: input.soul,
            voiceAdapter: this.dependencies.voiceAdapter
          }
        );
      } else {
        updatedTask = await this.dependencies.taskService.runTaskFromChat({
          profileId: input.profileId,
          taskId: selectedTask.id
        });
        if (!updatedTask) {
          responseContent = await this.dependencies.voiceAdapter.render({
            scene: "task.control_not_found",
            channel: "chat",
            soul: input.soul,
            facts: {},
            fallbackText: "我没有找到可开始的排队任务。"
          });
        } else if (updatedTask.status === "completed") {
          responseContent = (await buildTaskSettledMessageDraftWithVoice(
            updatedTask,
            this.dependencies.taskService.listTaskEvents(updatedTask.id),
            {
              soul: input.soul,
              voiceAdapter: this.dependencies.voiceAdapter
            }
          )).content;
        } else if (updatedTask.status === "failed") {
          responseContent = (await buildTaskSettledMessageDraftWithVoice(
            updatedTask,
            this.dependencies.taskService.listTaskEvents(updatedTask.id),
            {
              soul: input.soul,
              voiceAdapter: this.dependencies.voiceAdapter
            }
          )).content;
        } else {
          responseContent = await buildTaskStatusResponseWithVoice(
            updatedTask,
            this.dependencies.taskService.listTaskEvents(updatedTask.id),
            {
              soul: input.soul,
              voiceAdapter: this.dependencies.voiceAdapter
            }
          );
        }
      }
    } else if (request.action === "interrupt") {
      updatedTask = this.dependencies.taskService.interruptTaskFromChat({
        profileId: input.profileId,
        taskId: selectedTask.id,
        reason: "用户在主聊天线程要求停掉"
      });
      responseContent = updatedTask
        ? await this.dependencies.voiceAdapter.render({
            scene: "task.interrupted",
            channel: "chat",
            soul: input.soul,
            facts: {
              taskTitle: updatedTask.title,
              status: updatedTask.status
            },
            fallbackText: "好，我先停下来。你想换方向的话，直接告诉我。"
          })
        : await this.dependencies.voiceAdapter.render({
            scene: "task.control_not_found",
            channel: "chat",
            soul: input.soul,
            facts: {},
            fallbackText: "这件事现在不用停，我这边没有可打断的运行状态。"
          });
    } else {
      const revisedTitle = request.revisedTitle ?? input.content;
      updatedTask = this.dependencies.taskService.reviseTaskFromChat({
        profileId: input.profileId,
        taskId: selectedTask.id,
        title: revisedTitle
      });
      responseContent = updatedTask
        ? await this.dependencies.voiceAdapter.render({
            scene: "task.accepted",
            channel: "chat",
            soul: input.soul,
            facts: {
              taskTitle: updatedTask.title
            },
            fallbackText: `好，我按这个新方向来：${updatedTask.title}`
          })
        : await this.dependencies.voiceAdapter.render({
            scene: "task.control_not_found",
            channel: "chat",
            soul: input.soul,
            facts: {},
            fallbackText: "这个任务没改成，我没有找到可修改的任务。"
          });
    }

    const assistantMessage = this.dependencies.messageRepository.create({
      sessionId: input.sessionId,
      profileId: input.profileId,
      role: "assistant",
      content: responseContent
    });

    this.dependencies.eventLogService.record("chat.message_persisted", {
      profileId: input.profileId,
      sessionId: input.sessionId,
      userMessageId: input.userMessageId,
      assistantMessageId: assistantMessage.id,
      writtenMemoryIds: [],
      updatedTaskId: updatedTask?.id ?? null,
      taskControlAction: request.action,
      taskControlTargetHint: request.targetHint ?? null,
      gateDecision: "reply"
    });

    return {
      assistantMessage,
      updatedTask
    };
  }

}

function dedupeMemoryNodes(memories: MemoryNode[]): MemoryNode[] {
  const seen = new Set<string>();
  const result: MemoryNode[] = [];

  for (const memory of memories) {
    if (seen.has(memory.id)) {
      continue;
    }

    seen.add(memory.id);
    result.push(memory);
  }

  return result;
}
