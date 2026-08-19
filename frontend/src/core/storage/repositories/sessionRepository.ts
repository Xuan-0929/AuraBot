/**
 * 主聊天会话仓库：创建和读取每个 profile 的唯一 main session。
 * ChatOrchestrator 通过它定位消息归属；旧 Task 子会话和 session link 写入能力已移除。
 */
import { randomUUID } from "node:crypto";
import type { AppDatabase } from "../db";

export interface SessionRecord {
  id: string;
  profileId: string;
  title: string;
  createdAt: string;
  updatedAt: string;
}

interface SessionRow {
  id: string;
  profile_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export class SessionRepository {
  constructor(private readonly database: AppDatabase) {}

  getOrCreateMainSession(profileId: string): SessionRecord {
    const existing = this.database
      .prepare("select * from sessions where profile_id = ? limit 1")
      .get(profileId) as SessionRow | undefined;

    if (existing) {
      return mapSession(existing);
    }

    const now = new Date().toISOString();
    const session: SessionRecord = {
      id: `session_${randomUUID()}`,
      profileId,
      title: "Main chat",
      createdAt: now,
      updatedAt: now
    };

    this.database
      .prepare(
        `
        insert into sessions (
          id, profile_id, title, created_at, updated_at
        )
        values (@id, @profileId, @title, @createdAt, @updatedAt)
      `
      )
      .run(session);

    return session;
  }

  getSession(sessionId: string): SessionRecord | undefined {
    const row = this.database.prepare("select * from sessions where id = ?").get(sessionId) as
      | SessionRow
      | undefined;

    return row ? mapSession(row) : undefined;
  }
}

function mapSession(row: SessionRow): SessionRecord {
  return {
    id: row.id,
    profileId: row.profile_id,
    title: row.title,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}
