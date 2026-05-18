import type { Task, TaskEvent, TaskKind } from "../../shared/schemas/task";
import type { TaskRunner } from "./taskRunner";
import type { SessionRepository } from "../storage/repositories/sessionRepository";
import type { TaskRepository } from "../storage/repositories/taskRepository";
import type { EventLogService } from "../telemetry/eventLogService";
import { selectTaskForControl } from "./taskControlRouter";
import { isCancelableTask, isDeletableTask, isRunnableTask } from "./taskState";

export interface CreateTaskFromChatInput {
  profileId: string;
  parentSessionId: string;
  title: string;
  kind?: TaskKind;
  dueAt?: string;
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

export interface RequeueTaskFromChatInput {
  profileId: string;
  taskId?: string;
  targetHint?: string;
  reason: string;
}

export interface RescheduleReminderFromChatInput {
  profileId: string;
  taskId?: string;
  targetHint?: string;
  dueAt: string;
  reason: string;
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
      kind: input.kind ?? "result_output",
      title: input.title,
      status: "queued",
      latestProgress: input.kind === "time_reminder" ? "Reminder scheduled from main chat." : "Task queued from main chat.",
      dueAt: input.dueAt
    });

    this.eventLogService.record("task.created", {
      profileId: input.profileId,
      parentSessionId: input.parentSessionId,
      taskSessionId: taskSession.id,
      taskId: task.id,
      kind: task.kind,
      dueAt: task.dueAt ?? null,
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

  listDueReminderTasks(profileId: string, nowIso: string): Task[] {
    return this.taskRepository.listDueReminderTasks(profileId, nowIso);
  }

  getLatestTaskForProfile(profileId: string): Task | undefined {
    return this.taskRepository.getLatestByProfile(profileId);
  }

  getTaskById(taskId: string): Task | undefined {
    return this.taskRepository.getById(taskId);
  }

  completeReminderTask(input: { profileId: string; taskId: string; summary: string }): Task | undefined {
    const task = this.taskRepository.getById(input.taskId);

    if (!task || task.profileId !== input.profileId || task.kind !== "time_reminder" || task.status !== "queued") {
      return undefined;
    }

    this.taskRepository.appendEvent({
      taskId: task.id,
      eventType: "task.time_reminder_triggered",
      summary: input.summary,
      payload: {
        dueAt: task.dueAt ?? null
      }
    });

    const completed = this.taskRepository.updateStatus({
      taskId: task.id,
      status: "completed",
      latestProgress: input.summary
    });

    if (completed) {
      this.eventLogService.record("task.time_reminder_triggered", {
        profileId: input.profileId,
        taskId: completed.id,
        title: completed.title,
        dueAt: completed.dueAt ?? null
      });
    }

    return completed;
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

    if (!task || task.profileId !== input.profileId || !isCancelableTask(task.status)) {
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

    if (!task || task.profileId !== input.profileId || !isDeletableTask(task.status)) {
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

  requeueTaskFromChat(input: RequeueTaskFromChatInput): Task | undefined {
    const task = input.taskId
      ? this.taskRepository.getById(input.taskId)
      : this.findTaskForChatControl({
          profileId: input.profileId,
          targetHint: input.targetHint
        });

    if (!task || task.profileId !== input.profileId || task.kind === "time_reminder") {
      return undefined;
    }

    if (task.status === "queued") {
      return task;
    }

    const requeued = this.taskRepository.transitionStatus({
      taskId: task.id,
      from: ["failed", "interrupted"],
      to: "queued",
      latestProgress: input.reason
    });

    if (requeued) {
      this.taskRepository.appendEvent({
        taskId: requeued.id,
        eventType: "task.requeued_from_chat",
        summary: input.reason,
        payload: {
          previousStatus: task.status
        }
      });
      this.eventLogService.record("task.requeued_from_chat", {
        profileId: input.profileId,
        taskId: requeued.id,
        title: requeued.title,
        previousStatus: task.status,
        status: requeued.status
      });
    }

    return requeued;
  }

  rescheduleReminderFromChat(input: RescheduleReminderFromChatInput): Task | undefined {
    const task = input.taskId
      ? this.taskRepository.getById(input.taskId)
      : this.findTaskForChatControl({
          profileId: input.profileId,
          targetHint: input.targetHint
        });

    if (!task || task.profileId !== input.profileId || task.kind !== "time_reminder" || task.status !== "queued") {
      return undefined;
    }

    const updated = this.taskRepository.updateDueAt({
      taskId: task.id,
      dueAt: input.dueAt,
      latestProgress: input.reason
    });

    if (updated) {
      this.taskRepository.appendEvent({
        taskId: updated.id,
        eventType: "task.reminder_rescheduled",
        summary: input.reason,
        payload: {
          previousDueAt: task.dueAt ?? null,
          dueAt: updated.dueAt ?? null
        }
      });
      this.eventLogService.record("task.reminder_rescheduled", {
        profileId: input.profileId,
        taskId: updated.id,
        title: updated.title,
        previousDueAt: task.dueAt ?? null,
        dueAt: updated.dueAt ?? null
      });
    }

    return updated;
  }

  async runLatestQueuedTask(profileId: string): Promise<Task | undefined> {
    const task = this.taskRepository.getLatestActiveByProfile(profileId);

    if (!task || !isRunnableTask(task.status) || task.kind === "time_reminder") {
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

    if (!task || !isRunnableTask(task.status) || task.kind === "time_reminder") {
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

    if (!task || task.kind === "time_reminder") {
      return task;
    }

    if (!isRunnableTask(task.status)) {
      return task;
    }

    const runningSummary = `开始处理：${task.title}`;
    const startedTask = this.taskRepository.transitionStatus({
      taskId: task.id,
      from: "queued",
      to: "running",
      latestProgress: runningSummary
    });

    if (!startedTask) {
      return this.taskRepository.getById(task.id);
    }

    const runPromise = this.taskRunner.run(startedTask, {
      onProgress: input.onProgress
    });
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
