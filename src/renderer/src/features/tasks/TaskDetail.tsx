import type { Task } from "../../../../shared/schemas/task";

export function TaskDetail({ task }: { task: Task }) {
  return (
    <section className="task-detail">
      <h2>{task.title}</h2>
      <p>{task.latestProgress}</p>
    </section>
  );
}
