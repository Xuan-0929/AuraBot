import type { SendChatMessageInput, SendChatMessageResult } from "../../shared/contracts/chat";
import type { MemoryNode } from "../../shared/schemas/memory";
import { extractMemoryCandidates, shouldAskMemoryRecall } from "../memory/memoryExtractor";
import type { MemoryService } from "../memory/memoryService";
import type { MessageRepository } from "../storage/repositories/messageRepository";
import type { SessionRepository } from "../storage/repositories/sessionRepository";
import type { TaskService } from "../tasks/taskService";
import type { ProfileService } from "../profiles/profileService";
import type { EventLogService } from "../telemetry/eventLogService";
import { parseTaskControlRequest } from "../tasks/taskControlRouter";
import { evaluateGate } from "./gateEvaluator";
import { planAssistantResponse } from "./responsePlanner";

export interface ChatOrchestratorDependencies {
  profileService: ProfileService;
  sessionRepository: SessionRepository;
  messageRepository: MessageRepository;
  memoryService: MemoryService;
  taskService: TaskService;
  eventLogService: EventLogService;
}

export class ChatOrchestrator {
  constructor(private readonly dependencies: ChatOrchestratorDependencies) {}

  async sendMessage(input: SendChatMessageInput): Promise<SendChatMessageResult> {
    const profile = this.dependencies.profileService.getOrSeedActiveProfile();
    const mainSession = this.dependencies.sessionRepository.getOrCreateMainSession(profile.id);

    const userMessage = this.dependencies.messageRepository.create({
      sessionId: mainSession.id,
      profileId: profile.id,
      role: "user",
      content: input.content
    });
    const recentUserMessages = this.dependencies.messageRepository
      .listRecentUserMessages(mainSession.id, 4)
      .filter((message) => message.id !== userMessage.id)
      .map((message) => message.content);
    const gateDecision = evaluateGate({
      currentMessage: input.content,
      recentUserMessages
    });

    this.dependencies.eventLogService.record("gate.decision", {
      profileId: profile.id,
      sessionId: mainSession.id,
      replyDecision: gateDecision.replyDecision,
      inputKind: gateDecision.inputKind,
      memoryDecision: gateDecision.memoryDecision,
      confidence: gateDecision.confidence,
      reason: gateDecision.reason
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
        content: `我先把这个任务排上：${createdTask.title}`
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
        content: input.content
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
    const memoryContext = this.dependencies.memoryService.retrieveRelevant(profile.id, semanticSegment);
    const writtenMemories = memoryContext.nodes.filter((node) => writtenMemoryIds.includes(node.id));
    const recalledMemories = memoryContext.nodes.filter((node) => !writtenMemoryIds.includes(node.id));

    if (memoryContext.nodes.length > 0 && shouldAskMemoryRecall(semanticSegment)) {
      this.dependencies.eventLogService.record("memory.recall_used", {
        profileId: profile.id,
        sessionId: mainSession.id,
        nodeIds: memoryContext.nodes.map((node) => node.id),
        summaryTopics: memoryContext.summaries.map((summary) => summary.topic)
      });
    }

    const assistantMessage = this.dependencies.messageRepository.create({
      sessionId: mainSession.id,
      profileId: profile.id,
      role: "assistant",
      content: planAssistantResponse({
        userContent: semanticSegment,
        writtenMemories,
        recalledMemories: shouldAskMemoryRecall(semanticSegment) ? memoryContext.nodes : recalledMemories
      })
    });

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
    const candidates = extractMemoryCandidates(input.content);
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
  }) {
    const request = parseTaskControlRequest(input.content);
    const selectedTask = this.dependencies.taskService.findTaskForChatControl({
      profileId: input.profileId,
      targetHint: request.targetHint,
      activeOnly: request.action === "start" || request.action === "interrupt"
    });
    let updatedTask;
    let responseContent: string;

    if (!selectedTask) {
      responseContent = request.targetHint
        ? `我没有找到和「${request.targetHint}」匹配的任务。`
        : "我这边还没有正在跟的任务。";
    } else if (request.action === "query") {
      responseContent = `这个任务现在是 ${selectedTask.status}：${selectedTask.title}`;
    } else if (request.action === "start") {
      if (selectedTask.status !== "queued") {
        updatedTask = selectedTask;
        responseContent = `这个任务现在是 ${selectedTask.status}，不用重复开始：${selectedTask.title}`;
      } else {
        updatedTask = await this.dependencies.taskService.runTaskFromChat({
          profileId: input.profileId,
          taskId: selectedTask.id
        });
        responseContent = updatedTask
          ? `这个任务我跑完了：${updatedTask.latestProgress}`
          : "我没有找到可开始的排队任务。";
      }
    } else if (request.action === "interrupt") {
      updatedTask = this.dependencies.taskService.interruptTaskFromChat({
        profileId: input.profileId,
        taskId: selectedTask.id,
        reason: "用户在主聊天线程要求停掉"
      });
      responseContent = updatedTask
        ? `行，我先把这个任务停下：${updatedTask.title}`
        : `这个任务现在是 ${selectedTask.status}，没有可打断的运行态。`;
    } else {
      const revisedTitle = request.revisedTitle ?? input.content;
      updatedTask = this.dependencies.taskService.reviseTaskFromChat({
        profileId: input.profileId,
        taskId: selectedTask.id,
        title: revisedTitle
      });
      responseContent = updatedTask
        ? `我把任务方向改成：${updatedTask.title}`
        : "这个任务没改成，我没有找到可修改的任务。";
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
