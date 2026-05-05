import type { Task } from "../../../../shared/schemas/task";

export function TaskStatusOverlay({
  tasks,
  onCancelTask,
  onDeleteTask
}: {
  tasks: Task[];
  onCancelTask?: (taskId: string) => void;
  onDeleteTask?: (taskId: string) => void;
}) {
  const visibleTasks = tasks.slice(0, 3);

  if (visibleTasks.length === 0) {
    return null;
  }

  return (
    <aside className="task-status-overlay" aria-label="Task status">
      {visibleTasks.map((task) => (
        <div className="task-status-card" key={task.id}>
          <span className={`task-status-dot task-status-dot-${task.status}`} />
          <span className="task-status-copy">
            <span className="task-status-title">{formatTaskTitle(task.title)}</span>
            <span className="task-status-progress">{formatTaskProgress(task)}</span>
          </span>
          <span className="task-status-value">{formatTaskStatus(task.status)}</span>
          <TaskStatusAction task={task} onCancelTask={onCancelTask} onDeleteTask={onDeleteTask} />
        </div>
      ))}
    </aside>
  );
}

function TaskStatusAction({
  task,
  onCancelTask,
  onDeleteTask
}: {
  task: Task;
  onCancelTask?: (taskId: string) => void;
  onDeleteTask?: (taskId: string) => void;
}) {
  const title = formatTaskTitle(task.title);

  if (isTaskActive(task.status)) {
    return (
      <button
        className="task-status-action task-status-action-cancel"
        type="button"
        aria-label={`取消任务：${title}`}
        onClick={() => onCancelTask?.(task.id)}
      >
        取消
      </button>
    );
  }

  if (isTaskSettled(task.status)) {
    return (
      <button
        className="task-status-action task-status-action-delete"
        type="button"
        aria-label={`删除任务：${title}`}
        onClick={() => onDeleteTask?.(task.id)}
      >
        删除
      </button>
    );
  }

  return null;
}

function isTaskActive(status: Task["status"]): boolean {
  return status === "queued" || status === "running" || status === "waiting";
}

function isTaskSettled(status: Task["status"]): boolean {
  return status === "completed" || status === "failed" || status === "interrupted";
}

function formatTaskStatus(status: Task["status"]): string {
  if (status === "queued") {
    return "待开始";
  }

  if (status === "running" || status === "waiting") {
    return "处理中";
  }

  if (status === "completed") {
    return "完成";
  }

  if (status === "failed") {
    return "没跑成";
  }

  return "已停下";
}

function formatTaskProgress(task: Task): string {
  if (task.status === "queued") {
    return "还没开始";
  }

  if (task.status === "completed") {
    return "结果已经准备好";
  }

  if (task.status === "failed") {
    return "遇到一点问题";
  }

  if (task.status === "interrupted") {
    return "已经停下";
  }

  return cleanTaskProgress(task.latestProgress);
}

function cleanTaskProgress(progress: string): string {
  const cleaned = progress
    .replace(/^Codex 子任务会话已建立：.*$/u, "已经接上任务线程")
    .replace(/^Codex 子任务开始处理任务。?$/u, "正在处理")
    .replace(/^开始调用 Codex 子任务：.*$/u, "正在处理")
    .replace(/^Codex 子任务完成命令：.*$/u, "刚完成一步处理")
    .replace(/^Codex 子任务(?:新增|修改|删除)文件：.*$/u, "正在整理结果文件")
    .replace(/^Codex 子任务本轮处理完成，正在收尾。?$/u, "正在收尾")
    .trim();

  return cleaned || "正在处理";
}

function formatTaskTitle(title: string): string {
  const cleaned = title
    .trim()
    .replace(/[。.!！]*$/u, "")
    .replace(/^(请|麻烦)\s*/u, "")
    .replace(
      /^(帮我把|帮我做一下|帮我写一下|帮我整理一下|帮我生成一下|帮我做|帮我写|帮我整理|帮我生成|帮我|做一下|写一下|整理一下|生成一下|做|写|整理|生成)\s*/u,
      ""
    )
    .replace(/^(一份|一个|一篇|一版|这份|这个|一下)\s*/u, "")
    .trim();

  return cleaned || title;
}
