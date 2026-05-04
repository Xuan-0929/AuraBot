import type { Task } from "../../shared/schemas/task";
import type { TaskRepository } from "../storage/repositories/taskRepository";
import type { EventLogService } from "../telemetry/eventLogService";

export interface TaskRunner {
  run(task: Task): Promise<Task | undefined>;
}

export class DeterministicTaskRunner implements TaskRunner {
  constructor(
    private readonly taskRepository: TaskRepository,
    private readonly eventLogService: EventLogService
  ) {}

  async run(task: Task): Promise<Task | undefined> {
    const runningSummary = `开始处理：${task.title}`;
    this.taskRepository.appendEvent({
      taskId: task.id,
      eventType: "task.running",
      summary: runningSummary
    });
    this.taskRepository.updateStatus({
      taskId: task.id,
      status: "running",
      latestProgress: runningSummary
    });

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
    this.taskRepository.updateStatus({
      taskId: task.id,
      status: "running",
      latestProgress: progressSummary
    });

    const completedSummary = `已完成：${task.title}`;
    this.taskRepository.appendEvent({
      taskId: task.id,
      eventType: "task.completed",
      summary: completedSummary
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
