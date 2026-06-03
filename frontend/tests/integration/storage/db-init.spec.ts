import Database from "better-sqlite3";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createAppDatabase } from "@core/storage/db";

const tempDirs: string[] = [];

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { force: true, recursive: true });
  }
});

describe("createAppDatabase", () => {
  it("initializes core, task, and memory tables in a fresh SQLite file", () => {
    const dir = mkdtempSync(join(tmpdir(), "aurabot-db-"));
    tempDirs.push(dir);

    const database = createAppDatabase({ filePath: join(dir, "aurabot.sqlite") });
    const tables = database
      .prepare("select name from sqlite_master where type = 'table' order by name")
      .all()
      .map((row) => (row as { name: string }).name);

    database.close();

    expect(tables).toEqual(
      expect.arrayContaining([
        "profiles",
        "souls",
        "sessions",
        "messages",
        "tasks",
        "memory_nodes",
        "memory_edges",
        "memory_summaries",
        "memory_write_queue",
        "event_logs",
        "schema_migrations"
      ])
    );
  });

  it("migrates an existing v1 task table by adding task session ids", () => {
    const dir = mkdtempSync(join(tmpdir(), "aurabot-db-"));
    tempDirs.push(dir);
    const filePath = join(dir, "aurabot.sqlite");
    const oldDatabase = new Database(filePath);
    oldDatabase.exec(`
      create table schema_migrations (
        id integer primary key,
        name text not null,
        applied_at text not null
      );
      insert into schema_migrations (id, name, applied_at)
      values (1, 'initial_core_memory_task_schema', '2026-05-04T00:00:00.000Z');

      create table tasks (
        id text primary key,
        profile_id text not null,
        parent_session_id text not null,
        title text not null,
        status text not null,
        latest_progress text not null default '',
        created_at text not null,
        updated_at text not null
      );
    `);
    oldDatabase.close();

    const database = createAppDatabase({ filePath });
    const columns = database
      .prepare("pragma table_info(tasks)")
      .all()
      .map((row) => (row as { name: string }).name);
    const migrations = database
      .prepare("select id from schema_migrations order by id")
      .all()
      .map((row) => (row as { id: number }).id);

    database.close();

    expect(columns).toContain("session_id");
    expect(migrations).toContain(2);
  });
});
