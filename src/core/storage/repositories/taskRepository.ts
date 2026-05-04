import { randomUUID } from "node:crypto";
import type { Task, TaskEvent, TaskStatus } from "../../../shared/schemas/task";
import type { AppDatabase } from "../db";

interface TaskRow {
  id: string;
  profile_id: string;
  parent_session_id: string;
  session_id: string;
  title: string;
  status: TaskStatus;
  latest_progress: string;
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
    title: string;
    status: TaskStatus;
    latestProgress: string;
  }): Task {
    const now = new Date().toISOString();
    const task: Task = {
      id: `task_${randomUUID()}`,
      profileId: input.profileId,
      parentSessionId: input.parentSessionId,
      sessionId: input.sessionId,
      title: input.title,
      status: input.status,
      latestProgress: input.latestProgress,
      createdAt: now,
      updatedAt: now
    };

    this.database
      .prepare(
        `
        insert into tasks (
          id, profile_id, parent_session_id, session_id, title, status, latest_progress, created_at, updated_at
        )
        values (
          @id, @profileId, @parentSessionId, @sessionId, @title, @status, @latestProgress, @createdAt, @updatedAt
        )
      `
      )
      .run(task);

    return task;
  }

  listByProfile(profileId: string): Task[] {
    return (
      this.database
        .prepare("select * from tasks where profile_id = ? order by created_at desc")
        .all(profileId) as TaskRow[]
    ).map(mapTask);
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
}

function mapTask(row: TaskRow): Task {
  return {
    id: row.id,
    profileId: row.profile_id,
    parentSessionId: row.parent_session_id,
    sessionId: row.session_id,
    title: row.title,
    status: row.status,
    latestProgress: row.latest_progress,
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
