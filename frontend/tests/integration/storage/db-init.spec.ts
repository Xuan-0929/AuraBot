/**
 * 验证应用数据库的新基线与旧 Task Runtime 破坏性迁移。
 *
 * 测试使用真实 SQLite、临时 userData 目录和文件系统副作用，覆盖 canonical Main
 * Session、Task 数据清理、受控目录删除、失败语义与重试；它不依赖旧 Task repositories。
 */

import Database from "better-sqlite3";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createAppDatabase } from "@core/storage/db";

const tempDirs: string[] = [];

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    const lockedUserData = join(dir, "locked-user-data");
    if (existsSync(lockedUserData)) {
      chmodSync(lockedUserData, 0o700);
    }
    chmodSync(dir, 0o700);
    rmSync(dir, { force: true, recursive: true });
  }
});

function makeTempRoot(): string {
  const root = mkdtempSync(join(tmpdir(), "aurabot-db-"));
  tempDirs.push(root);
  return root;
}

function tableNames(database: Database.Database): string[] {
  return database
    .prepare("select name from sqlite_master where type = 'table' order by name")
    .all()
    .map((row) => (row as { name: string }).name);
}

function tableSql(database: Database.Database, name: string): string | undefined {
  return (
    database
      .prepare("select sql from sqlite_master where type = 'table' and name = ?")
      .get(name) as { sql: string } | undefined
  )?.sql;
}

function indexSql(database: Database.Database, name: string): string | undefined {
  return (
    database
      .prepare("select sql from sqlite_master where type = 'index' and name = ?")
      .get(name) as { sql: string } | undefined
  )?.sql;
}

function columnNames(database: Database.Database, table: string): string[] {
  return database
    .prepare(`pragma table_info(${table})`)
    .all()
    .map((row) => (row as { name: string }).name);
}

function createLegacyDatabase(filePath: string): void {
  const database = new Database(filePath);
  database.pragma("foreign_keys = ON");
  database.exec(`
    create table schema_migrations (
      id integer primary key,
      name text not null,
      applied_at text not null
    );
    insert into schema_migrations (id, name, applied_at) values
      (1, 'initial_core_memory_task_schema', '2026-05-04T00:00:00.000Z'),
      (2, 'add_task_session_id_to_existing_task_tables', '2026-05-05T00:00:00.000Z'),
      (3, 'add_message_metadata_json', '2026-05-06T00:00:00.000Z'),
      (4, 'add_soul_voice_fields', '2026-05-07T00:00:00.000Z'),
      (5, 'add_task_kind_and_due_at', '2026-05-08T00:00:00.000Z'),
      (6, 'add_persona_settings_fields_to_souls', '2026-05-09T00:00:00.000Z'),
      (7, 'add_task_input_artifacts', '2026-05-10T00:00:00.000Z');

    create table profiles (
      id text primary key,
      display_name text not null,
      active integer not null,
      created_at text not null,
      updated_at text not null
    );
    create table sessions (
      id text primary key,
      profile_id text not null references profiles(id) on delete cascade,
      kind text not null check (kind in ('main', 'task')),
      title text not null,
      parent_session_id text references sessions(id) on delete set null,
      depth integer not null default 0,
      created_at text not null,
      updated_at text not null
    );
    create table session_links (
      id text primary key,
      parent_session_id text not null references sessions(id) on delete cascade,
      child_session_id text not null references sessions(id) on delete cascade,
      relation_type text not null,
      created_at text not null
    );
    create table messages (
      id text primary key,
      session_id text not null references sessions(id) on delete cascade,
      profile_id text not null references profiles(id) on delete cascade,
      role text not null,
      content text not null,
      metadata_json text not null default '{}',
      created_at text not null
    );
    create index idx_messages_session_created on messages(session_id, created_at);
    create table memory_write_queue (
      id text primary key,
      profile_id text not null references profiles(id) on delete cascade,
      source_session_id text not null references sessions(id) on delete cascade,
      payload_json text not null,
      status text not null check (status in ('queued', 'running', 'completed', 'failed')),
      created_at text not null,
      updated_at text not null
    );
    create table tasks (
      id text primary key,
      profile_id text not null references profiles(id) on delete cascade,
      parent_session_id text not null references sessions(id) on delete cascade,
      session_id text not null references sessions(id) on delete cascade,
      task_kind text not null,
      title text not null,
      status text not null,
      latest_progress text not null,
      due_at text,
      created_at text not null,
      updated_at text not null
    );
    create table task_events (
      id text primary key,
      task_id text not null references tasks(id) on delete cascade,
      event_type text not null,
      payload_json text not null,
      created_at text not null
    );
    create table task_artifacts (
      id text primary key,
      task_id text not null references tasks(id) on delete cascade,
      artifact_type text not null,
      uri text not null,
      created_at text not null
    );
    create table task_input_artifacts (
      id text primary key,
      task_id text not null references tasks(id) on delete cascade,
      artifact_type text not null,
      original_name text not null,
      mime_type text not null,
      size_bytes integer not null,
      uri text not null,
      created_at text not null
    );
    create table event_logs (
      id text primary key,
      event_type text not null,
      payload_json text not null,
      created_at text not null
    );

    insert into profiles values ('profile', 'User', 1, 'now', 'now');
    insert into sessions values ('main', 'profile', 'main', 'Main', null, 0, 'now', 'now');
    insert into sessions values ('task-session', 'profile', 'task', 'Task', 'main', 1, 'now', 'now');
    insert into session_links values ('link', 'main', 'task-session', 'delegated_task', 'now');
    insert into messages values ('main-message', 'main', 'profile', 'user', 'keep', '{}', 'now');
    insert into messages values ('task-message', 'task-session', 'profile', 'assistant', 'drop', '{}', 'now');
    insert into memory_write_queue values (
      'main-memory-write', 'profile', 'main', '{"keep":true}', 'queued', 'now', 'now'
    );
    insert into memory_write_queue values (
      'task-memory-write', 'profile', 'task-session', '{"drop":true}', 'queued', 'now', 'now'
    );
    insert into tasks values (
      'task', 'profile', 'main', 'task-session', 'result_output', 'Legacy', 'completed', '',
      null, 'now', 'now'
    );
    insert into task_events values ('task-event', 'task', 'task.completed', '{}', 'now');
    insert into task_artifacts values ('artifact', 'task', 'file', 'file:///tmp/output', 'now');
    insert into task_input_artifacts values (
      'input', 'task', 'file', 'input.txt', 'text/plain', 1, 'file:///tmp/input', 'now'
    );
    insert into event_logs values ('keep-event', 'chat.message.completed', '{}', 'now');
    insert into event_logs values ('drop-event', 'task.completed', '{}', 'now');
  `);
  database.close();
}

function markLegacyTaskRuntimeRemoved(filePath: string): void {
  const database = new Database(filePath);
  database.pragma("foreign_keys = ON");
  database.transaction(() => {
    database.exec(`
      drop table if exists task_input_artifacts;
      drop table if exists task_artifacts;
      drop table if exists task_events;
      drop table if exists tasks;
      delete from sessions where kind = 'task';
      delete from event_logs where event_type like 'task.%';
    `);
    database.prepare(
      "insert into schema_migrations (id, name, applied_at) values (8, ?, ?)"
    ).run("remove_legacy_task_runtime_data", "2026-08-19T00:00:00.000Z");
  })();
  database.close();
}

describe("createAppDatabase", () => {
  it("creates a fresh core and memory schema without Task tables", () => {
    const root = makeTempRoot();
    const database = createAppDatabase({
      filePath: join(root, "aurabot.sqlite"),
      userDataPath: root
    });
    const tables = tableNames(database);
    const sessionColumns = columnNames(database, "sessions");

    database.close();

    expect(tables).toEqual(
      expect.arrayContaining([
        "profiles",
        "souls",
        "sessions",
        "messages",
        "memory_nodes",
        "memory_edges",
        "memory_summaries",
        "memory_write_queue",
        "event_logs",
        "schema_migrations"
      ])
    );
    expect(tables).not.toEqual(
      expect.arrayContaining([
        "tasks",
        "task_events",
        "task_artifacts",
        "task_input_artifacts",
        "session_links"
      ])
    );
    expect(sessionColumns).toEqual([
      "id",
      "profile_id",
      "title",
      "created_at",
      "updated_at"
    ]);
  });

  it("produces identical key schema for fresh and upgraded databases", () => {
    const root = makeTempRoot();
    const fresh = createAppDatabase({
      filePath: join(root, "fresh.sqlite"),
      userDataPath: join(root, "fresh-user-data")
    });
    const legacyFilePath = join(root, "legacy.sqlite");
    createLegacyDatabase(legacyFilePath);
    const upgraded = createAppDatabase({
      filePath: legacyFilePath,
      userDataPath: join(root, "legacy-user-data")
    });

    for (const table of ["sessions", "messages", "memory_write_queue"]) {
      expect(tableSql(upgraded, table)).toBe(tableSql(fresh, table));
    }
    expect(indexSql(upgraded, "idx_messages_session_created")).toBe(
      indexSql(fresh, "idx_messages_session_created")
    );
    expect(columnNames(upgraded, "sessions")).toEqual(columnNames(fresh, "sessions"));
    expect(upgraded.pragma("foreign_key_list(sessions)")).toEqual(
      fresh.pragma("foreign_key_list(sessions)")
    );
    expect(tableNames(upgraded)).not.toContain("session_links");
    expect(
      upgraded.prepare("select count(*) from schema_migrations where id = 9").pluck().get()
    ).toBe(1);
    expect(upgraded.pragma("foreign_key_check")).toEqual([]);

    fresh.close();
    upgraded.close();
  });

  it("removes legacy Task data and directories while preserving unrelated data", () => {
    const root = makeTempRoot();
    const filePath = join(root, "aurabot.sqlite");
    createLegacyDatabase(filePath);
    mkdirSync(join(root, "task-inputs"));
    mkdirSync(join(root, "task-agent-output"));
    mkdirSync(join(root, "keep"));
    writeFileSync(join(root, "keep", "data.txt"), "keep");

    const database = createAppDatabase({ filePath, userDataPath: root });

    expect(tableNames(database)).not.toEqual(
      expect.arrayContaining(["tasks", "task_events", "task_artifacts", "task_input_artifacts"])
    );
    expect(database.prepare("select id from sessions order by id").pluck().all()).toEqual(["main"]);
    expect(database.prepare("select id from messages order by id").pluck().all()).toEqual([
      "main-message"
    ]);
    expect(tableNames(database)).not.toContain("session_links");
    expect(
      database.prepare("select id from memory_write_queue order by id").pluck().all()
    ).toEqual(["main-memory-write"]);
    expect(database.prepare("select id from event_logs order by id").pluck().all()).toEqual([
      "keep-event"
    ]);
    expect(database.pragma("foreign_key_check")).toEqual([]);
    expect(existsSync(join(root, "task-inputs"))).toBe(false);
    expect(existsSync(join(root, "task-agent-output"))).toBe(false);
    expect(readFileSync(join(root, "keep", "data.txt"), "utf8")).toBe("keep");

    database.close();
  });

  it("unlinks a controlled symlink without deleting its target", () => {
    const root = makeTempRoot();
    const target = join(root, "outside-target");
    mkdirSync(target);
    writeFileSync(join(target, "keep.txt"), "keep");
    symlinkSync(target, join(root, "task-inputs"));

    const database = createAppDatabase({
      filePath: join(root, "aurabot.sqlite"),
      userDataPath: root
    });
    database.close();

    expect(existsSync(join(root, "task-inputs"))).toBe(false);
    expect(readFileSync(join(target, "keep.txt"), "utf8")).toBe("keep");
  });

  it("keeps database migrations committed when cleanup fails and succeeds on retry", () => {
    const root = makeTempRoot();
    const userDataPath = join(root, "locked-user-data");
    const filePath = join(root, "aurabot.sqlite");
    mkdirSync(join(userDataPath, "task-inputs"), { recursive: true });
    createLegacyDatabase(filePath);
    chmodSync(userDataPath, 0o500);

    expect(() => createAppDatabase({ filePath, userDataPath })).toThrow(/Task runtime cleanup/);

    const failedDatabase = new Database(filePath);
    expect(tableNames(failedDatabase)).not.toContain("tasks");
    expect(
      failedDatabase.prepare("select count(*) from schema_migrations where id = 8").pluck().get()
    ).toBe(1);
    expect(
      failedDatabase.prepare("select count(*) from schema_migrations where id = 9").pluck().get()
    ).toBe(1);
    failedDatabase.close();

    chmodSync(userDataPath, 0o700);
    const retriedDatabase = createAppDatabase({ filePath, userDataPath });
    expect(tableNames(retriedDatabase)).not.toContain("tasks");
    expect(
      retriedDatabase.prepare("select count(*) from schema_migrations where id = 8").pluck().get()
    ).toBe(1);
    retriedDatabase.close();
  });

  it("preserves an honest partial cleanup state and retries the remaining directory", () => {
    const root = makeTempRoot();
    const filePath = join(root, "aurabot.sqlite");
    createLegacyDatabase(filePath);
    mkdirSync(join(root, "task-inputs"));
    mkdirSync(join(root, "task-agent-output"));

    expect(() =>
      createAppDatabase({
        filePath,
        userDataPath: root,
        legacyTaskEntryRemover(candidate: string, directoryName: string) {
          if (directoryName === "task-agent-output") {
            throw new Error("simulated second-directory failure");
          }
          rmSync(candidate, { force: true, recursive: true });
        }
      })
    ).toThrow(/simulated second-directory failure/);

    expect(existsSync(join(root, "task-inputs"))).toBe(false);
    expect(existsSync(join(root, "task-agent-output"))).toBe(true);
    const migratedDatabase = new Database(filePath);
    expect(tableNames(migratedDatabase)).not.toContain("tasks");
    expect(
      migratedDatabase.prepare("select id from schema_migrations where id in (8, 9) order by id").pluck().all()
    ).toEqual([8, 9]);
    migratedDatabase.close();

    const retriedDatabase = createAppDatabase({ filePath, userDataPath: root });
    retriedDatabase.close();
    expect(existsSync(join(root, "task-agent-output"))).toBe(false);
  });

  it("retries filesystem cleanup after migration 8 was already recorded", () => {
    const root = makeTempRoot();
    const filePath = join(root, "aurabot.sqlite");
    createLegacyDatabase(filePath);
    markLegacyTaskRuntimeRemoved(filePath);
    mkdirSync(join(root, "task-inputs"));
    mkdirSync(join(root, "task-agent-output"));

    const database = createAppDatabase({ filePath, userDataPath: root });
    database.close();

    expect(existsSync(join(root, "task-inputs"))).toBe(false);
    expect(existsSync(join(root, "task-agent-output"))).toBe(false);
  });
});
