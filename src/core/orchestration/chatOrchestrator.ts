import { randomUUID } from "node:crypto";
import type { SendChatMessageInput, SendChatMessageResult } from "../../shared/contracts/chat";
import type { Message } from "../../shared/schemas/message";
import type { MemoryNode } from "../../shared/schemas/memory";
import type { Soul } from "../../shared/schemas/profile";
import { shouldAskMemoryRecall, type MemoryExtractor } from "../memory/memoryExtractor";
import type { MemoryService } from "../memory/memoryService";
import type { MessageRepository } from "../storage/repositories/messageRepository";
import type { SessionRepository } from "../storage/repositories/sessionRepository";
import type { TaskService } from "../tasks/taskService";
import type { RunTaskInBackgroundInput } from "../tasks/taskService";
import type { TaskInputAttachmentService } from "../tasks/taskInputAttachments";
import type { ProfileService } from "../profiles/profileService";
import type { EventLogService } from "../telemetry/eventLogService";
import { parseTaskControlRequest } from "../tasks/taskControlRouter";
import { parseReminderRequest } from "../tasks/reminderParser";
import type { GateModel } from "../llm/gateModel";
import type { ChatModel } from "../llm/chatModel";
import type { VoiceAdapter } from "../voice/voiceAdapter";
import {
  ModelConfigurationError,
  ModelInvocationError,
  summarizeModelError,
  type ModelRoleName
} from "../llm/modelErrors";
import {
  buildTaskResultAttachments,
  buildTaskSettledMessageDraft,
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
  taskInputAttachmentService: TaskInputAttachmentService;
  eventLogService: EventLogService;
  gateModel: GateModel;
  chatModel: ChatModel;
  voiceAdapter: VoiceAdapter;
  startTaskInBackground?: (input: RunTaskInBackgroundInput) => ReturnType<TaskService["runTaskInBackground"]>;
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

  setTaskBackgroundStarter(
    startTaskInBackground: (input: RunTaskInBackgroundInput) => ReturnType<TaskService["runTaskInBackground"]>
  ): void {
    this.dependencies.startTaskInBackground = startTaskInBackground;
  }

  async sendMessage(input: SendChatMessageInput, options: SendChatMessageOptions = {}): Promise<SendChatMessageResult> {
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
      .map((message) => ({
        role: message.role,
        content: message.content
      }));
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
        if (gateDecision.taskKind === "time_reminder") {
          const reminder = parseReminderRequest({
            message: semanticSegment,
            nowIso: new Date().toISOString()
          });

          if (!reminder) {
            assistantMessage = this.dependencies.messageRepository.create({
              sessionId: mainSession.id,
              profileId: profile.id,
              role: "assistant",
              content: await this.dependencies.voiceAdapter.render({
                scene: "task.reminder_schedule_failed",
                channel: "chat",
                soul: profile.soul,
                facts: {
                  taskTitle: semanticSegment
                }
              })
            });

            this.dependencies.eventLogService.record("chat.message_persisted", {
              profileId: profile.id,
              sessionId: mainSession.id,
              userMessageId: userMessage.id,
              assistantMessageId: assistantMessage.id,
              writtenMemoryIds: [],
              gateDecision: gateDecision.replyDecision
            });

            return {
              userMessage,
              assistantMessage,
              agentStatus: "idle",
              gateDecision
            };
          }

          const createdTask = this.dependencies.taskService.createTaskFromChat({
            profileId: profile.id,
            parentSessionId: mainSession.id,
            title: reminder.content,
            kind: "time_reminder",
            dueAt: reminder.dueAt
          });
          assistantMessage = this.dependencies.messageRepository.create({
            sessionId: mainSession.id,
            profileId: profile.id,
            role: "assistant",
            content: await this.dependencies.voiceAdapter.render({
              scene: "task.reminder_scheduled",
              channel: "chat",
              soul: profile.soul,
              facts: {
                taskTitle: reminder.content,
                dueAt: reminder.dueAt
              }
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
            agentStatus: "idle",
            gateDecision,
            createdTask
          };
        }

        const createdTask = this.dependencies.taskService.createTaskFromChat({
          profileId: profile.id,
          parentSessionId: mainSession.id,
          title: semanticSegment,
          kind: "result_output"
        });
        this.dependencies.taskInputAttachmentService.stageForTask(createdTask.id, input.attachments);
        assistantMessage = this.dependencies.messageRepository.create({
          sessionId: mainSession.id,
          profileId: profile.id,
          role: "assistant",
          content: await this.dependencies.voiceAdapter.render({
            scene: "task.accepted",
            channel: "chat",
            soul: profile.soul,
            facts: {
              taskTitle: semanticSegment
            }
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
          updatedTask: taskControlResult.updatedTask,
          deletedTaskId: taskControlResult.deletedTaskId
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
              messageId: assistantMessage!.id,
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
    let deletedTaskId: string | undefined;
    let attachments: Message["attachments"] = [];
    let responseContent: string;

    if (!selectedTask) {
      responseContent = request.targetHint
        ? await this.dependencies.voiceAdapter.render({
            scene: "task.control_not_found",
            channel: "chat",
            soul: input.soul,
            facts: {
              targetHint: request.targetHint
            }
          })
        : await this.dependencies.voiceAdapter.render({
            scene: "task.control_not_found",
            channel: "chat",
            soul: input.soul,
            facts: {}
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
        updatedTask = (this.dependencies.startTaskInBackground ?? this.dependencies.taskService.runTaskInBackground.bind(this.dependencies.taskService))({
          profileId: input.profileId,
          taskId: selectedTask.id
        });
        if (!updatedTask) {
          responseContent = await this.dependencies.voiceAdapter.render({
            scene: "task.control_not_found",
            channel: "chat",
            soul: input.soul,
            facts: {}
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
        ? (await buildTaskSettledMessageDraftWithVoice(
            updatedTask,
            this.dependencies.taskService.listTaskEvents(updatedTask.id),
            {
              soul: input.soul,
              voiceAdapter: this.dependencies.voiceAdapter
            }
          )).content
        : await this.dependencies.voiceAdapter.render({
            scene: "task.control_not_found",
            channel: "chat",
            soul: input.soul,
            facts: {}
          });
    } else if (request.action === "continue" || request.action === "retry" || request.action === "rerun") {
      const queuedTask = this.dependencies.taskService.requeueTaskFromChat({
        profileId: input.profileId,
        taskId: selectedTask.id,
        reason: "用户在主聊天线程要求继续处理"
      }) ?? selectedTask;
      if (queuedTask.status === "queued") {
        updatedTask = (this.dependencies.startTaskInBackground ?? this.dependencies.taskService.runTaskInBackground.bind(this.dependencies.taskService))({
          profileId: input.profileId,
          taskId: queuedTask.id
        });
      } else {
        updatedTask = queuedTask;
      }
      responseContent = updatedTask
        ? await buildTaskStatusResponseWithVoice(
            updatedTask,
            this.dependencies.taskService.listTaskEvents(updatedTask.id),
            {
              soul: input.soul,
              voiceAdapter: this.dependencies.voiceAdapter
            }
          )
        : await this.dependencies.voiceAdapter.render({
            scene: "task.control_not_found",
            channel: "chat",
            soul: input.soul,
            facts: {}
          });
    } else if (request.action === "open_result") {
      updatedTask = selectedTask;
      const events = this.dependencies.taskService.listTaskEvents(selectedTask.id);
      const draft = buildTaskSettledMessageDraft(selectedTask, events);
      attachments = buildTaskResultAttachments(selectedTask.id, events);
      responseContent = attachments.length > 0
        ? draft.content
        : await buildTaskStatusResponseWithVoice(selectedTask, events, {
            soul: input.soul,
            voiceAdapter: this.dependencies.voiceAdapter
          });
    } else if (request.action === "delete") {
      updatedTask = selectedTask;
      const deleted = this.dependencies.taskService.deleteSettledTaskFromConsole({
        profileId: input.profileId,
        taskId: selectedTask.id
      });
      if (deleted) {
        deletedTaskId = selectedTask.id;
        updatedTask = undefined;
      }
      responseContent = deleted
        ? `我把“${selectedTask.title}”从任务便签里移除了。`
        : await this.dependencies.voiceAdapter.render({
            scene: "task.control_not_found",
            channel: "chat",
            soul: input.soul,
            facts: {}
          });
    } else {
      const revisedTitle = request.revisedTitle ?? input.content;
      if (selectedTask.kind === "time_reminder") {
        const reminder = parseReminderRequest({
          message: `${revisedTitle}提醒我${selectedTask.title}`,
          nowIso: new Date().toISOString()
        });
        updatedTask = reminder
          ? this.dependencies.taskService.rescheduleReminderFromChat({
              profileId: input.profileId,
              taskId: selectedTask.id,
              dueAt: reminder.dueAt,
              reason: "用户在主聊天线程调整提醒时间"
            })
          : undefined;
      } else {
        updatedTask = this.dependencies.taskService.reviseTaskFromChat({
          profileId: input.profileId,
          taskId: selectedTask.id,
          title: revisedTitle
        });
      }
      responseContent = updatedTask
        ? await this.dependencies.voiceAdapter.render({
            scene: selectedTask.kind === "time_reminder" ? "task.reminder_scheduled" : "task.accepted",
            channel: "chat",
            soul: input.soul,
            facts: {
              taskTitle: updatedTask.title,
              dueAt: updatedTask.dueAt
            }
          })
        : await this.dependencies.voiceAdapter.render({
            scene: "task.control_not_found",
            channel: "chat",
            soul: input.soul,
            facts: {}
          });
    }

    const assistantMessage = this.dependencies.messageRepository.create({
      sessionId: input.sessionId,
      profileId: input.profileId,
      role: "assistant",
      content: responseContent,
      attachments
    });

    this.dependencies.eventLogService.record("chat.message_persisted", {
      profileId: input.profileId,
      sessionId: input.sessionId,
      userMessageId: input.userMessageId,
      assistantMessageId: assistantMessage.id,
      writtenMemoryIds: [],
      updatedTaskId: updatedTask?.id ?? null,
      deletedTaskId: deletedTaskId ?? null,
      taskControlAction: request.action,
      taskControlTargetHint: request.targetHint ?? null,
      gateDecision: "reply"
    });

    return {
      assistantMessage,
      updatedTask,
      deletedTaskId
    };
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
    const assistantMessage =
      input.assistantMessage
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

    this.dependencies.eventLogService.record("chat.message_persisted", {
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

function buildModelRecoveryMessage(error: unknown): string {
  const summary = summarizeModelError(error);
  const role = getModelRoleFromError(error);

  if (role === "memoryModel") {
    return "我这边记忆模型现在没连好，这句我先不敢乱记。你可以先继续聊，或者去设置里测一下记忆模型连接。";
  }

  if (role === "gateModel") {
    if (summary.code === "authentication_failed") {
      return "我这边判断模型没连好，这句没法稳稳接住。你去设置里检查一下判断模型的 API Key，再测一下连接，然后把这句话重新发我一次。";
    }

    return "我这边判断模型刚刚没连上，这句没能稳稳接住。你去设置里测试一下判断模型连接，连好后再发我一次。";
  }

  if (summary.code === "authentication_failed") {
    return "我这边聊天模型现在没连好，API Key 这边像是有点问题。你去设置里检查一下聊天模型配置，再测一下连接，然后我就能继续接着回你。";
  }

  if (summary.code === "missing_configuration" || summary.code === "unsupported_provider") {
    return "我这边聊天模型还没接好，先没法正常回你。你去设置里看一下聊天模型配置，再测试一下连接，连上之后再发我一次。";
  }

  if (role === "voiceAdapter") {
    return "我这边回复模型刚刚掉了一下线，这句没能正常说出来。你去设置里测一下聊天模型连接，我们马上继续。";
  }

  return "我这边聊天模型刚刚没连上，这句先没法好好接住。你去设置里测一下聊天模型连接，连好后我继续。";
}

function getModelRoleFromError(error: unknown): ModelRoleName | undefined {
  if (error instanceof ModelConfigurationError || error instanceof ModelInvocationError) {
    return error.role;
  }

  return undefined;
}
