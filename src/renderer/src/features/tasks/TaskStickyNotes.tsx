import type { PetSettings } from "../../../../shared/schemas/petSettings";
import type { Task } from "../../../../shared/schemas/task";
import { t, taskProgressLabel } from "../../i18n";

export function TaskStickyNotes({
  tasks,
  displayLanguage,
  variant,
  onCancelTask: _onCancelTask,
  onDeleteTask: _onDeleteTask
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
