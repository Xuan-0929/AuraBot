import type { Task } from "../../../../shared/schemas/task";
import type { PetSettings } from "../../../../shared/schemas/petSettings";
import { t, taskProgressLabel, taskStatusLabel } from "../../i18n";

export function TaskStatusOverlay({
  tasks,
  displayLanguage = "en",
  onCancelTask,
  onDeleteTask
}: {
  tasks: Task[];
  displayLanguage?: PetSettings["displayLanguage"];
  onCancelTask?: (taskId: string) => void;
  onDeleteTask?: (taskId: string) => void;
}) {
  const visibleTasks = tasks.slice(0, 3);

  if (visibleTasks.length === 0) {
    return null;
  }

  return (
    <aside className="task-status-overlay" aria-label={t(displayLanguage, "taskStatus")}>
      {visibleTasks.map((task) => (
        <div className="task-status-card" key={task.id}>
          <span className={`task-status-dot task-status-dot-${task.status}`} />
          <span className="task-status-copy">
            <span className="task-status-title">{formatTaskTitle(task.title)}</span>
            <span className="task-status-progress">{taskProgressLabel(displayLanguage, task)}</span>
          </span>
          <span className="task-status-value">{taskStatusLabel(displayLanguage, task.status)}</span>
          <TaskStatusAction
            task={task}
            displayLanguage={displayLanguage}
            onCancelTask={onCancelTask}
            onDeleteTask={onDeleteTask}
          />
        </div>
      ))}
    </aside>
  );
}

function TaskStatusAction({
  task,
  displayLanguage,
  onCancelTask,
  onDeleteTask
}: {
  task: Task;
  displayLanguage: PetSettings["displayLanguage"];
  onCancelTask?: (taskId: string) => void;
  onDeleteTask?: (taskId: string) => void;
}) {
  const title = formatTaskTitle(task.title);

  if (isTaskActive(task.status)) {
    return (
      <button
        className="task-status-action task-status-action-cancel"
        type="button"
        aria-label={`${t(displayLanguage, "cancelTask")}: ${title}`}
        onClick={() => onCancelTask?.(task.id)}
      >
        {t(displayLanguage, "cancel")}
      </button>
    );
  }

  if (isTaskSettled(task.status)) {
    return (
      <button
        className="task-status-action task-status-action-delete"
        type="button"
        aria-label={`${t(displayLanguage, "deleteTask")}: ${title}`}
        onClick={() => onDeleteTask?.(task.id)}
      >
        {t(displayLanguage, "delete")}
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
