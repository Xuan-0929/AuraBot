import { randomUUID } from "node:crypto";
import type { Message, MessageRole } from "../../../shared/schemas/message";
import type { AppDatabase } from "../db";

interface MessageRow {
  id: string;
  session_id: string;
  profile_id: string;
  role: MessageRole;
  content: string;
  created_at: string;
}

export class MessageRepository {
  constructor(private readonly database: AppDatabase) {}

  listBySession(sessionId: string): Message[] {
    return (
      this.database
        .prepare("select * from messages where session_id = ? order by created_at asc")
        .all(sessionId) as MessageRow[]
    ).map(mapMessage);
  }

  listRecentUserMessages(sessionId: string, limit: number): Message[] {
    return (
      this.database
        .prepare(
          `
          select * from messages
          where session_id = ? and role = 'user'
          order by created_at desc
          limit ?
        `
        )
        .all(sessionId, limit) as MessageRow[]
    )
      .map(mapMessage)
      .reverse();
  }

  create(input: {
    sessionId: string;
    profileId: string;
    role: MessageRole;
    content: string;
  }): Message {
    const now = new Date().toISOString();
    const message: Message = {
      id: `message_${randomUUID()}`,
      sessionId: input.sessionId,
      profileId: input.profileId,
      role: input.role,
      content: input.content,
      createdAt: now
    };

    this.database
      .prepare(
        `
        insert into messages (id, session_id, profile_id, role, content, created_at)
        values (@id, @sessionId, @profileId, @role, @content, @createdAt)
      `
      )
      .run(message);

    return message;
  }
}

function mapMessage(row: MessageRow): Message {
  return {
    id: row.id,
    sessionId: row.session_id,
    profileId: row.profile_id,
    role: row.role,
    content: row.content,
    createdAt: row.created_at
  };
}
