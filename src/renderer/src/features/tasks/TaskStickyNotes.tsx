import type { PetSettings } from "../../../../shared/schemas/petSettings";
import type { Task } from "../../../../shared/schemas/task";
import { t, taskProgressLabel } from "../../i18n";

export function TaskStickyNotes({
  tasks,
  displayLanguage,
  variant,
  onCancelTask,
  onDeleteTask
}: {
  tasks: Task[];
  displayLanguage: PetSettings["displayLanguage"];
  variant: "pet" | "console";
  onCancelTask?: (taskId: string) => void;
  onDeleteTask?: (taskId: string) => void;
}) {
  const visibleTasks = variant === "pet" ? pickVisiblePetTasks(tasks) : tasks;

  if (visibleTasks.length === 0) {
    return null;
  }

  return (
    <aside
      className={[
        "task-sticky-notes",
        `task-sticky-notes-${variant}`,
        variant === "pet" ? "desktop-pet-sticky-notes" : undefined
      ]
        .filter(Boolean)
        .join(" ")}
      aria-label={t(displayLanguage, "taskStickyNotes")}
    >
      <header className="task-sticky-notes-header">
        <strong>{t(displayLanguage, "taskStickyNotes")}</strong>
        <span>{t(displayLanguage, "taskStickyNoteHint")}</span>
      </header>
      {visibleTasks.map((task) => (
        <article key={task.id} className="task-sticky-note">
          <div className="task-sticky-note-copy">
            <strong>{summarizeTitle(task.title)}</strong>
            <span>{summarizeMeta(task, displayLanguage)}</span>
          </div>
          {variant === "console" ? (
            <TaskStickyNoteActions
              task={task}
              displayLanguage={displayLanguage}
              onCancelTask={onCancelTask}
              onDeleteTask={onDeleteTask}
            />
          ) : null}
        </article>
      ))}
    </aside>
  );
}

function summarizeTitle(title: string): string {
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

function summarizeMeta(task: Task, displayLanguage: PetSettings["displayLanguage"]): string {
  return taskProgressLabel(displayLanguage, task);
}

function pickVisiblePetTasks(tasks: Task[]): Task[] {
  return tasks.filter((task) => isActive(task.status)).slice(0, 2);
}

function TaskStickyNoteActions({
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
  const title = summarizeTitle(task.title);

  if (isActive(task.status) && onCancelTask) {
    return (
      <div className="task-sticky-note-actions">
        <button
          className="task-sticky-note-action task-sticky-note-action-cancel"
          type="button"
          aria-label={`${t(displayLanguage, "cancelTask")}: ${title}`}
          onClick={() => onCancelTask(task.id)}
        >
          {t(displayLanguage, "cancel")}
        </button>
      </div>
    );
  }

  if (isSettled(task.status) && onDeleteTask) {
    return (
      <div className="task-sticky-note-actions">
        <button
          className="task-sticky-note-action task-sticky-note-action-delete"
          type="button"
          aria-label={`${t(displayLanguage, "deleteTask")}: ${title}`}
          onClick={() => onDeleteTask(task.id)}
        >
          {t(displayLanguage, "delete")}
        </button>
      </div>
    );
  }

  return null;
}

function isActive(status: Task["status"]): boolean {
  return status === "queued" || status === "running" || status === "waiting";
}

function isSettled(status: Task["status"]): boolean {
  return status === "completed" || status === "failed" || status === "interrupted";
}
