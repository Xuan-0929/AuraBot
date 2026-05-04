import type { Task } from "../../../../shared/schemas/task";

export function TaskStatusOverlay({ tasks }: { tasks: Task[] }) {
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
            <span className="task-status-title">{task.title}</span>
            <span className="task-status-progress">{task.latestProgress}</span>
          </span>
          <span className="task-status-value">{task.status}</span>
        </div>
      ))}
    </aside>
  );
}
