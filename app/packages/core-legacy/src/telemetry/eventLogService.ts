import { randomUUID } from "node:crypto";
import type { EventLogRecord } from "../../../shared/src/contracts/ipc";
import type { AppDatabase } from "../storage/db";

interface EventLogRow {
  id: string;
  event_type: string;
  payload_json: string;
  created_at: string;
}

export class EventLogService {
  constructor(private readonly database: AppDatabase) {}

  record(eventType: string, payload: Record<string, unknown>): EventLogRecord {
    const record: EventLogRecord = {
      id: `event_${randomUUID()}`,
      eventType,
      payload,
      createdAt: new Date().toISOString()
    };

    this.database
      .prepare(
        "insert into event_logs (id, event_type, payload_json, created_at) values (?, ?, ?, ?)"
      )
      .run(record.id, record.eventType, JSON.stringify(record.payload), record.createdAt);

    return record;
  }

  list(limit = 100): EventLogRecord[] {
    return (
      this.database
        .prepare("select * from event_logs order by created_at desc limit ?")
        .all(limit) as EventLogRow[]
    ).map((row) => ({
      id: row.id,
      eventType: row.event_type,
      payload: JSON.parse(row.payload_json) as Record<string, unknown>,
      createdAt: row.created_at
    }));
  }
}
