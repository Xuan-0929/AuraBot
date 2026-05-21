import type { Task } from "../../../shared/src/schemas/task";
import type { TaskRepository } from "../storage/repositories/taskRepository";
import type { EventLogService } from "../telemetry/eventLogService";

export interface TaskRunner {
  run(task: Task, observer?: TaskRunnerObserver): Promise<Task | undefined>;
  cancel?(taskId: string): boolean;
}

export interface TaskRunnerObserver {
  onProgress?: (task: Task) => void;
}

export class DeterministicTaskRunner implements TaskRunner {
  constructor(
    private readonly taskRepository: TaskRepository,
    private readonly eventLogService: EventLogService
  ) {}

  async run(task: Task, observer?: TaskRunnerObserver): Promise<Task | undefined> {
    const startedAt = new Date();
    const runningSummary = `开始处理：${task.title}`;
    this.taskRepository.appendEvent({
      taskId: task.id,
      eventType: "task.running",
      summary: runningSummary,
      payload: {
        startedAt: startedAt.toISOString()
      }
    });
    const running = this.taskRepository.updateStatus({
      taskId: task.id,
      status: "running",
      latestProgress: runningSummary
    });

    if (running) {
      observer?.onProgress?.(running);
    }

    const progressSummary = `已拆出任务步骤，正在整理：${task.title}`;
    this.taskRepository.appendEvent({
      taskId: task.id,
      eventType: "task.progress",
      summary: progressSummary,
      payload: {
        step: 1,
        totalSteps: 1
      }
    });
    const progressed = this.taskRepository.updateStatus({
      taskId: task.id,
      status: "running",
      latestProgress: progressSummary
    });

    if (progressed) {
      observer?.onProgress?.(progressed);
    }

    const completedSummary = `已完成：${task.title}`;
    const completedAt = new Date();
    this.taskRepository.appendEvent({
      taskId: task.id,
      eventType: "task.completed",
      summary: completedSummary,
      payload: {
        startedAt: startedAt.toISOString(),
        completedAt: completedAt.toISOString(),
        durationMs: completedAt.getTime() - startedAt.getTime()
      }
    });
    const completed = this.taskRepository.updateStatus({
      taskId: task.id,
      status: "completed",
      latestProgress: completedSummary
    });

    if (completed) {
      this.eventLogService.record("task.completed", {
        profileId: completed.profileId,
        taskId: completed.id,
        title: completed.title,
        status: completed.status
      });
    }

    return completed;
  }
}
