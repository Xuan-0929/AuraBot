/**
 * 主聊天会话仓库：创建和读取每个 profile 的唯一 main session。
 * ChatOrchestrator 通过它定位消息归属；旧 Task 子会话和 session link 写入能力已移除。
 */
import { randomUUID } from "node:crypto";
import type { AppDatabase } from "../db";

export interface SessionRecord {
  id: string;
  profileId: string;
  kind: "main";
  title: string;
  depth: number;
  createdAt: string;
  updatedAt: string;
}

interface SessionRow {
  id: string;
  profile_id: string;
  kind: "main";
  title: string;
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
        values (@id, @profileId, @kind, @title, null, @depth, @createdAt, @updatedAt)
      `
      )
      .run(session);

    return session;
  }

  getSession(sessionId: string): SessionRecord | undefined {
    const row = this.database.prepare("select * from sessions where id = ? and kind = 'main'").get(sessionId) as
      | SessionRow
      | undefined;

    return row ? mapSession(row) : undefined;
  }
}

function mapSession(row: SessionRow): SessionRecord {
  return {
    id: row.id,
    profileId: row.profile_id,
    kind: row.kind,
    title: row.title,
    depth: row.depth,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}
