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
  const visibleTasks = pickVisibleTasks(tasks, variant);

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
          <strong>{summarizeTitle(task.title)}</strong>
          <span>{summarizeMeta(task, displayLanguage)}</span>
          {variant === "console" ? (
            <div className="task-sticky-note-actions">
              {isActive(task.status) ? (
                <button type="button" onClick={() => onCancelTask?.(task.id)}>
                  {t(displayLanguage, "cancel")}
                </button>
              ) : null}
              {isSettled(task.status) ? (
                <button type="button" onClick={() => onDeleteTask?.(task.id)}>
                  {t(displayLanguage, "delete")}
                </button>
              ) : null}
            </div>
          ) : null}
        </article>
      ))}
    </aside>
  );
}

function pickVisibleTasks(tasks: Task[], variant: "pet" | "console"): Task[] {
  if (variant === "pet") {
    return tasks.filter((task) => isActive(task.status)).slice(0, 2);
  }

  return tasks.slice(0, 3);
}

function summarizeTitle(title: string): string {
  const cleaned = title
    .trim()
    .replace(/[。.!！]*$/u, "")
    .replace(/^(请你|请|麻烦你|麻烦|帮忙)\s*/u, "")
    .replace(/^(帮我把|帮我先把|帮我先|帮我给|帮我)\s*/u, "")
    .trim();

  return cleaned || title;
}

function summarizeMeta(task: Task, displayLanguage: PetSettings["displayLanguage"]): string {
  return taskProgressLabel(displayLanguage, task);
}

function isActive(status: Task["status"]): boolean {
  return status === "queued" || status === "running" || status === "waiting";
}

function isSettled(status: Task["status"]): boolean {
  return status === "completed" || status === "failed" || status === "interrupted";
}
