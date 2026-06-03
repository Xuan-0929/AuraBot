import { randomUUID } from "node:crypto";
import type { Message, MessageAttachment, MessageRole } from "@shared/schemas/message";
import type { AppDatabase } from "../db";

interface MessageRow {
  id: string;
  session_id: string;
  profile_id: string;
  role: MessageRole;
  content: string;
  metadata_json?: string;
  created_at: string;
}

export class MessageRepository {
  constructor(private readonly database: AppDatabase) {}

  listBySession(sessionId: string): Message[] {
    return (
      this.database
        .prepare("select * from messages where session_id = ? order by created_at asc, rowid asc")
        .all(sessionId) as MessageRow[]
    ).map(mapMessage);
  }

  listRecentBySession(sessionId: string, limit: number): Message[] {
    return (
      this.database
        .prepare(
          `
          select * from messages
          where session_id = ?
          order by created_at desc, rowid desc
          limit ?
        `
        )
        .all(sessionId, limit) as MessageRow[]
    )
      .map(mapMessage)
      .reverse();
  }

  listRecentUserMessages(sessionId: string, limit: number): Message[] {
    return (
      this.database
        .prepare(
          `
          select * from messages
          where session_id = ? and role = 'user'
          order by created_at desc, rowid desc
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
    attachments?: MessageAttachment[];
  }): Message {
    const now = new Date().toISOString();
    const message: Message = {
      id: `message_${randomUUID()}`,
      sessionId: input.sessionId,
      profileId: input.profileId,
      role: input.role,
      content: input.content,
      attachments: input.attachments ?? [],
      createdAt: now
    };

    this.database
      .prepare(
        `
        insert into messages (id, session_id, profile_id, role, content, metadata_json, created_at)
        values (@id, @sessionId, @profileId, @role, @content, @metadataJson, @createdAt)
      `
      )
      .run({
        ...message,
        metadataJson: JSON.stringify({
          attachments: message.attachments
        })
      });

    return message;
  }

  updateContent(input: { messageId: string; content: string }): Message | undefined {
    this.database
      .prepare(
        `
        update messages
        set content = @content
        where id = @messageId
      `
      )
      .run(input);

    const row = this.database
      .prepare("select * from messages where id = ?")
      .get(input.messageId) as MessageRow | undefined;

    return row ? mapMessage(row) : undefined;
  }
}

function mapMessage(row: MessageRow): Message {
  return {
    id: row.id,
    sessionId: row.session_id,
    profileId: row.profile_id,
    role: row.role,
    content: row.content,
    attachments: parseMessageAttachments(row.metadata_json),
    createdAt: row.created_at
  };
}

function parseMessageAttachments(metadataJson: string | undefined): MessageAttachment[] {
  if (!metadataJson) {
    return [];
  }

  try {
    const metadata = JSON.parse(metadataJson) as { attachments?: unknown };

    return Array.isArray(metadata.attachments) ? (metadata.attachments as MessageAttachment[]) : [];
  } catch {
    return [];
  }
}
