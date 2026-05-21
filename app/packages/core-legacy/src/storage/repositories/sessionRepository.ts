import { randomUUID } from "node:crypto";
import type { AppDatabase } from "../db";

export interface SessionRecord {
  id: string;
  profileId: string;
  kind: "main" | "task";
  title: string;
  parentSessionId?: string;
  depth: number;
  createdAt: string;
  updatedAt: string;
}

interface SessionRow {
  id: string;
  profile_id: string;
  kind: "main" | "task";
  title: string;
  parent_session_id: string | null;
  depth: number;
  created_at: string;
  updated_at: string;
}

export class SessionRepository {
  constructor(private readonly database: AppDatabase) {}

  getOrCreateMainSession(profileId: string): SessionRecord {
    const existing = this.database
      .prepare("select * from sessions where profile_id = ? and kind = 'main' limit 1")
      .get(profileId) as SessionRow | undefined;

    if (existing) {
      return mapSession(existing);
    }

    const now = new Date().toISOString();
    const session: SessionRecord = {
      id: `session_${randomUUID()}`,
      profileId,
      kind: "main",
      title: "Main chat",
      depth: 0,
      createdAt: now,
      updatedAt: now
    };

    this.database
      .prepare(
        `
        insert into sessions (
          id, profile_id, kind, title, parent_session_id, depth, created_at, updated_at
        )
        values (@id, @profileId, @kind, @title, @parentSessionId, @depth, @createdAt, @updatedAt)
      `
      )
      .run({ ...session, parentSessionId: session.parentSessionId ?? null });

    return session;
  }

  getSession(sessionId: string): SessionRecord | undefined {
    const row = this.database.prepare("select * from sessions where id = ?").get(sessionId) as
      | SessionRow
      | undefined;

    return row ? mapSession(row) : undefined;
  }

  createTaskSession(input: {
    profileId: string;
    parentSessionId: string;
    title: string;
  }): SessionRecord {
    const parent = this.getSession(input.parentSessionId);
    const now = new Date().toISOString();
    const session: SessionRecord = {
      id: `session_${randomUUID()}`,
      profileId: input.profileId,
      kind: "task",
      title: input.title,
      parentSessionId: input.parentSessionId,
      depth: (parent?.depth ?? 0) + 1,
      createdAt: now,
      updatedAt: now
    };

    const write = this.database.transaction(() => {
      this.database
        .prepare(
          `
          insert into sessions (
            id, profile_id, kind, title, parent_session_id, depth, created_at, updated_at
          )
          values (@id, @profileId, @kind, @title, @parentSessionId, @depth, @createdAt, @updatedAt)
        `
        )
        .run(session);

      this.database
        .prepare(
          `
          insert into session_links (id, parent_session_id, child_session_id, relation_type, created_at)
          values (@id, @parentSessionId, @childSessionId, 'task_child', @createdAt)
        `
        )
        .run({
          id: `session_link_${randomUUID()}`,
          parentSessionId: input.parentSessionId,
          childSessionId: session.id,
          createdAt: now
        });
    });

    write();
    return session;
  }
}

function mapSession(row: SessionRow): SessionRecord {
  return {
    id: row.id,
    profileId: row.profile_id,
    kind: row.kind,
    title: row.title,
    parentSessionId: row.parent_session_id ?? undefined,
    depth: row.depth,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}
