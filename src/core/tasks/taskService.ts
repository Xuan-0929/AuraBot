import type { Task, TaskEvent } from "../../shared/schemas/task";
import type { TaskRunner } from "./taskRunner";
import type { SessionRepository } from "../storage/repositories/sessionRepository";
import type { TaskRepository } from "../storage/repositories/taskRepository";
import type { EventLogService } from "../telemetry/eventLogService";
import { selectTaskForControl } from "./taskControlRouter";

const ACTIVE_STATUSES = new Set<Task["status"]>(["queued", "running", "waiting"]);
const SETTLED_STATUSES = new Set<Task["status"]>(["completed", "failed", "interrupted"]);

export interface CreateTaskFromChatInput {
  profileId: string;
  parentSessionId: string;
  title: string;
}

export interface InterruptLatestActiveTaskInput {
  profileId: string;
  reason: string;
}

export interface InterruptTaskFromChatInput {
  profileId: string;
  reason: string;
  taskId?: string;
  targetHint?: string;
}

export interface ReviseTaskFromChatInput {
  profileId: string;
  taskId?: string;
  targetHint?: string;
  title: string;
}

export interface RunTaskFromChatInput {
  profileId: string;
  taskId?: string;
  targetHint?: string;
}

export interface RunTaskInBackgroundInput extends RunTaskFromChatInput {
  onProgress?: (task: Task) => void;
  onSettled?: (task: Task) => void;
}

export class TaskService {
  constructor(
    private readonly taskRepository: TaskRepository,
    private readonly sessionRepository: SessionRepository,
    private readonly eventLogService: EventLogService,
    private taskRunner: TaskRunner
  ) {}

  setTaskRunner(taskRunner: TaskRunner): void {
    this.taskRunner = taskRunner;
  }

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

  recoverOrphanedActiveTasks(): Task[] {
    const activeTasks = this.taskRepository.listByStatuses(["running", "waiting"]);
    const recoveredTasks: Task[] = [];

    for (const task of activeTasks) {
      const summary = `任务在上次应用退出时失联，已标记失败：${task.title}`;
      this.taskRepository.appendEvent({
        taskId: task.id,
        eventType: "task.recovered_orphaned",
        summary,
        payload: {
          previousStatus: task.status,
          previousProgress: task.latestProgress
        }
      });
      const recovered = this.taskRepository.updateStatus({
        taskId: task.id,
        status: "failed",
        latestProgress: summary
      });

      if (recovered) {
        this.eventLogService.record("task.recovered_orphaned", {
          profileId: recovered.profileId,
          taskId: recovered.id,
          title: recovered.title,
          previousStatus: task.status,
          status: recovered.status
        });
        recoveredTasks.push(recovered);
      }
    }

    return recoveredTasks;
  }

  listTasksForProfile(profileId: string): Task[] {
    return this.taskRepository.listByProfile(profileId);
  }

  getLatestTaskForProfile(profileId: string): Task | undefined {
    return this.taskRepository.getLatestByProfile(profileId);
  }

  getTaskById(taskId: string): Task | undefined {
    return this.taskRepository.getById(taskId);
  }

  findTaskForChatControl(input: {
    profileId: string;
    targetHint?: string;
    activeOnly?: boolean;
  }): Task | undefined {
    return selectTaskForControl({
      tasks: this.taskRepository.listByProfile(input.profileId),
      targetHint: input.targetHint,
      activeOnly: input.activeOnly
    });
  }

  interruptLatestActiveTask(input: InterruptLatestActiveTaskInput): Task | undefined {
    const task = this.taskRepository.getLatestActiveByProfile(input.profileId);

    return this.interruptResolvedTask({
      profileId: input.profileId,
      task,
      reason: input.reason
    });
  }

  interruptTaskFromChat(input: InterruptTaskFromChatInput): Task | undefined {
    const task = input.taskId
      ? this.taskRepository.getById(input.taskId)
      : this.findTaskForChatControl({
          profileId: input.profileId,
          targetHint: input.targetHint,
          activeOnly: true
        });

    return this.interruptResolvedTask({
      profileId: input.profileId,
      task,
      reason: input.reason
    });
  }

  cancelTaskFromConsole(input: { profileId: string; taskId: string }): Task | undefined {
    const task = this.taskRepository.getById(input.taskId);

    if (!task || task.profileId !== input.profileId || !ACTIVE_STATUSES.has(task.status)) {
      return undefined;
    }

    const reason = "好，我先停下来了。之后你想继续的话，再跟我说一声。";
    const cancelled = this.taskRepository.updateStatus({
      taskId: task.id,
      status: "interrupted",
      latestProgress: reason
    });

    if (cancelled) {
      const runnerCancelled = this.taskRunner.cancel?.(cancelled.id) ?? false;
      this.taskRepository.appendEvent({
        taskId: cancelled.id,
        eventType: "task.cancelled_from_console",
        summary: reason,
        payload: {
          previousStatus: task.status,
          runnerCancelled
        }
      });
      this.eventLogService.record("task.cancelled_from_console", {
        profileId: input.profileId,
        taskId: cancelled.id,
        title: cancelled.title,
        previousStatus: task.status,
        status: cancelled.status,
        runnerCancelled
      });
    }

    return cancelled;
  }

  deleteSettledTaskFromConsole(input: { profileId: string; taskId: string }): boolean {
    const task = this.taskRepository.getById(input.taskId);

    if (!task || task.profileId !== input.profileId || !SETTLED_STATUSES.has(task.status)) {
      return false;
    }

    this.taskRepository.appendEvent({
      taskId: task.id,
      eventType: "task.deleted_from_console",
      summary: `从控制台移除任务：${task.title}`,
      payload: {
        deletedStatus: task.status
      }
    });
    const deleted = this.taskRepository.deleteById(task.id);

    if (deleted) {
      this.eventLogService.record("task.deleted_from_console", {
        profileId: input.profileId,
        taskId: task.id,
        title: task.title,
        deletedStatus: task.status
      });
    }

    return deleted;
  }

  private interruptResolvedTask(input: {
    profileId: string;
    task?: Task;
    reason: string;
  }): Task | undefined {
    const task = input.task;

    if (!task) {
      return undefined;
    }

    const interrupted = this.taskRepository.updateStatus({
      taskId: task.id,
      status: "interrupted",
      latestProgress: input.reason
    });

    if (interrupted) {
      const runnerCancelled = this.taskRunner.cancel?.(interrupted.id) ?? false;
      this.eventLogService.record("task.interrupted", {
        profileId: input.profileId,
        taskId: interrupted.id,
        reason: input.reason,
        status: interrupted.status,
        runnerCancelled
      });
    }

    return interrupted;
  }

  reviseTaskFromChat(input: ReviseTaskFromChatInput): Task | undefined {
    const task = input.taskId
      ? this.taskRepository.getById(input.taskId)
      : this.findTaskForChatControl({
          profileId: input.profileId,
          targetHint: input.targetHint
        });

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

  async runTaskFromChat(input: RunTaskFromChatInput): Promise<Task | undefined> {
    const task = input.taskId
      ? this.taskRepository.getById(input.taskId)
      : this.findTaskForChatControl({
          profileId: input.profileId,
          targetHint: input.targetHint,
          activeOnly: true
        });

    if (!task || task.status !== "queued") {
      return task;
    }

    return this.taskRunner.run(task);
  }

  runTaskInBackground(input: RunTaskInBackgroundInput): Task | undefined {
    const task = input.taskId
      ? this.taskRepository.getById(input.taskId)
      : this.findTaskForChatControl({
          profileId: input.profileId,
          targetHint: input.targetHint,
          activeOnly: true
        });

    if (!task || task.status !== "queued") {
      return task;
    }

    const runPromise = this.taskRunner.run(task, {
      onProgress: input.onProgress
    });
    const startedTask = this.taskRepository.getById(task.id);
    void runPromise.then((settledTask) => {
      if (settledTask) {
        input.onSettled?.(settledTask);
      }
    }).catch((error) => {
      const message = error instanceof Error ? error.message : String(error);
      const failedSummary = `任务执行异常：${message}`;
      this.taskRepository.appendEvent({
        taskId: task.id,
        eventType: "task.failed",
        summary: failedSummary
      });
      const failedTask = this.taskRepository.updateStatus({
        taskId: task.id,
        status: "failed",
        latestProgress: failedSummary
      });

      if (failedTask) {
        this.eventLogService.record("task.failed", {
          profileId: failedTask.profileId,
          taskId: failedTask.id,
          title: failedTask.title,
          status: failedTask.status,
          reason: message
        });
        input.onSettled?.(failedTask);
      }
    });

    return startedTask;
  }

  listTaskEvents(taskId: string): TaskEvent[] {
    return this.taskRepository.listEvents(taskId);
  }
}
