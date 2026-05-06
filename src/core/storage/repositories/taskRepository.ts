import { randomUUID } from "node:crypto";
import type { Task, TaskEvent, TaskKind, TaskStatus } from "../../../shared/schemas/task";
import type { AppDatabase } from "../db";

interface TaskRow {
  id: string;
  profile_id: string;
  parent_session_id: string;
  session_id: string;
  task_kind?: TaskKind;
  title: string;
  status: TaskStatus;
  latest_progress: string;
  due_at?: string | null;
  created_at: string;
  updated_at: string;
}

interface TaskEventRow {
  id: string;
  task_id: string;
  event_type: string;
  payload_json: string;
  created_at: string;
}

export class TaskRepository {
  constructor(private readonly database: AppDatabase) {}

  create(input: {
    profileId: string;
    parentSessionId: string;
    sessionId: string;
    kind?: TaskKind;
    title: string;
    status: TaskStatus;
    latestProgress: string;
    dueAt?: string;
  }): Task {
    const now = new Date().toISOString();
    const task: Task = {
      id: `task_${randomUUID()}`,
      profileId: input.profileId,
      parentSessionId: input.parentSessionId,
      sessionId: input.sessionId,
      kind: input.kind ?? "result_output",
      title: input.title,
      status: input.status,
      latestProgress: input.latestProgress,
      dueAt: input.dueAt,
      createdAt: now,
      updatedAt: now
    };

    this.database
      .prepare(
        `
        insert into tasks (
          id, profile_id, parent_session_id, session_id, task_kind, title, status, latest_progress, due_at, created_at, updated_at
        )
        values (
          @id, @profileId, @parentSessionId, @sessionId, @kind, @title, @status, @latestProgress, @dueAt, @createdAt, @updatedAt
        )
      `
      )
      .run({
        ...task,
        dueAt: task.dueAt ?? null
      });

    return task;
  }

  listByProfile(profileId: string): Task[] {
    return (
      this.database
        .prepare("select * from tasks where profile_id = ? order by created_at desc")
        .all(profileId) as TaskRow[]
    ).map(mapTask);
  }

  listByStatuses(statuses: TaskStatus[]): Task[] {
    if (statuses.length === 0) {
      return [];
    }

    const placeholders = statuses.map(() => "?").join(", ");

    return (
      this.database
        .prepare(`select * from tasks where status in (${placeholders}) order by updated_at asc`)
        .all(...statuses) as TaskRow[]
    ).map(mapTask);
  }

  listDueReminderTasks(profileId: string, nowIso: string): Task[] {
    const nowTime = new Date(nowIso).getTime();
    const tasks = (
      this.database
        .prepare(
          `
          select * from tasks
          where profile_id = ?
            and task_kind = 'time_reminder'
            and status = 'queued'
            and due_at is not null
          order by due_at asc
        `
        )
        .all(profileId) as TaskRow[]
    ).map(mapTask);

    return tasks.filter((task) => {
      const dueTime = task.dueAt ? new Date(task.dueAt).getTime() : Number.NaN;

      return Number.isFinite(nowTime) && Number.isFinite(dueTime) && dueTime <= nowTime;
    });
  }

  getLatestByProfile(profileId: string): Task | undefined {
    const row = this.database
      .prepare("select * from tasks where profile_id = ? order by created_at desc limit 1")
      .get(profileId) as TaskRow | undefined;

    return row ? mapTask(row) : undefined;
  }

  getLatestActiveByProfile(profileId: string): Task | undefined {
    const row = this.database
      .prepare(
        `
        select * from tasks
        where profile_id = ? and status in ('queued', 'running', 'waiting')
        order by created_at desc
        limit 1
      `
      )
      .get(profileId) as TaskRow | undefined;

    return row ? mapTask(row) : undefined;
  }

  updateStatus(input: { taskId: string; status: TaskStatus; latestProgress: string }): Task | undefined {
    const now = new Date().toISOString();
    this.database
      .prepare(
        `
        update tasks
        set status = @status,
            latest_progress = @latestProgress,
            updated_at = @updatedAt
        where id = @taskId
      `
      )
      .run({
        taskId: input.taskId,
        status: input.status,
        latestProgress: input.latestProgress,
        updatedAt: now
      });

    return this.getById(input.taskId);
  }

  appendEvent(input: {
    taskId: string;
    eventType: string;
    summary: string;
    payload?: Record<string, unknown>;
  }): TaskEvent {
    const event: TaskEvent = {
      id: `task_event_${randomUUID()}`,
      taskId: input.taskId,
      eventType: input.eventType,
      summary: input.summary,
      payload: input.payload ?? {},
      createdAt: new Date().toISOString()
    };

    this.database
      .prepare(
        `
        insert into task_events (id, task_id, event_type, payload_json, created_at)
        values (@id, @taskId, @eventType, @payloadJson, @createdAt)
      `
      )
      .run({
        id: event.id,
        taskId: event.taskId,
        eventType: event.eventType,
        payloadJson: JSON.stringify({
          ...event.payload,
          summary: event.summary
        }),
        createdAt: event.createdAt
      });

    return event;
  }

  listEvents(taskId: string): TaskEvent[] {
    return (
      this.database
        .prepare("select * from task_events where task_id = ? order by created_at asc")
        .all(taskId) as TaskEventRow[]
    ).map(mapTaskEvent);
  }

  revise(input: { taskId: string; title: string; latestProgress: string }): Task | undefined {
    const now = new Date().toISOString();
    this.database
      .prepare(
        `
        update tasks
        set title = @title,
            status = 'queued',
            latest_progress = @latestProgress,
            updated_at = @updatedAt
        where id = @taskId
      `
      )
      .run({
        taskId: input.taskId,
        title: input.title,
        latestProgress: input.latestProgress,
        updatedAt: now
      });

    return this.getById(input.taskId);
  }

  getById(taskId: string): Task | undefined {
    const row = this.database.prepare("select * from tasks where id = ?").get(taskId) as TaskRow | undefined;

    return row ? mapTask(row) : undefined;
  }

  deleteById(taskId: string): boolean {
    const result = this.database.prepare("delete from tasks where id = ?").run(taskId);

    return result.changes > 0;
  }
}

function mapTask(row: TaskRow): Task {
  return {
    id: row.id,
    profileId: row.profile_id,
    parentSessionId: row.parent_session_id,
    sessionId: row.session_id,
    kind: row.task_kind ?? "result_output",
    title: row.title,
    status: row.status,
    latestProgress: row.latest_progress,
    dueAt: row.due_at ?? undefined,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function mapTaskEvent(row: TaskEventRow): TaskEvent {
  const payload = JSON.parse(row.payload_json) as Record<string, unknown>;
  const summary = typeof payload.summary === "string" ? payload.summary : row.event_type;

  return {
    id: row.id,
    taskId: row.task_id,
    eventType: row.event_type,
    summary,
    payload,
    createdAt: row.created_at
  };
}
