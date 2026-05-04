import type { Task, TaskEvent } from "../../shared/schemas/task";
import type { TaskRunner } from "./taskRunner";
import type { SessionRepository } from "../storage/repositories/sessionRepository";
import type { TaskRepository } from "../storage/repositories/taskRepository";
import type { EventLogService } from "../telemetry/eventLogService";

export interface CreateTaskFromChatInput {
  profileId: string;
  parentSessionId: string;
  title: string;
}

export interface InterruptLatestActiveTaskInput {
  profileId: string;
  reason: string;
}

export interface ReviseTaskFromChatInput {
  profileId: string;
  taskId?: string;
  title: string;
}

export class TaskService {
  constructor(
    private readonly taskRepository: TaskRepository,
    private readonly sessionRepository: SessionRepository,
    private readonly eventLogService: EventLogService,
    private readonly taskRunner: TaskRunner
  ) {}

  createTaskFromChat(input: CreateTaskFromChatInput): Task {
    const taskSession = this.sessionRepository.createTaskSession({
      profileId: input.profileId,
      parentSessionId: input.parentSessionId,
      title: input.title
    });
    const task = this.taskRepository.create({
      profileId: input.profileId,
      parentSessionId: input.parentSessionId,
      sessionId: taskSession.id,
      title: input.title,
      status: "queued",
      latestProgress: "Task queued from main chat."
    });

    this.eventLogService.record("task.created", {
      profileId: input.profileId,
      parentSessionId: input.parentSessionId,
      taskSessionId: taskSession.id,
      taskId: task.id,
      title: input.title,
      status: task.status
    });

    return task;
  }

  listTasksForProfile(profileId: string): Task[] {
    return this.taskRepository.listByProfile(profileId);
  }

  getLatestTaskForProfile(profileId: string): Task | undefined {
    return this.taskRepository.getLatestByProfile(profileId);
  }

  interruptLatestActiveTask(input: InterruptLatestActiveTaskInput): Task | undefined {
    const task = this.taskRepository.getLatestActiveByProfile(input.profileId);

    if (!task) {
      return undefined;
    }

    const interrupted = this.taskRepository.updateStatus({
      taskId: task.id,
      status: "interrupted",
      latestProgress: input.reason
    });

    if (interrupted) {
      this.eventLogService.record("task.interrupted", {
        profileId: input.profileId,
        taskId: interrupted.id,
        reason: input.reason,
        status: interrupted.status
      });
    }

    return interrupted;
  }

  reviseTaskFromChat(input: ReviseTaskFromChatInput): Task | undefined {
    const task = input.taskId
      ? this.taskRepository.getById(input.taskId)
      : this.taskRepository.getLatestByProfile(input.profileId);

    if (!task) {
      return undefined;
    }

    const revised = this.taskRepository.revise({
      taskId: task.id,
      title: input.title,
      latestProgress: "Task revised from main chat."
    });

    if (revised) {
      this.eventLogService.record("task.revised", {
        profileId: input.profileId,
        taskId: revised.id,
        title: revised.title,
        status: revised.status
      });
    }

    return revised;
  }

  async runLatestQueuedTask(profileId: string): Promise<Task | undefined> {
    const task = this.taskRepository.getLatestActiveByProfile(profileId);

    if (!task || task.status !== "queued") {
      return task;
    }

    return this.taskRunner.run(task);
  }

  listTaskEvents(taskId: string): TaskEvent[] {
    return this.taskRepository.listEvents(taskId);
  }
}
