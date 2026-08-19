/**
 * 验证应用数据库的新基线与旧 Task Runtime 破坏性迁移。
 *
 * 测试使用真实 SQLite、临时 userData 目录和文件系统副作用，覆盖 Task 数据清理、
 * 受控目录删除、符号链接安全、失败回滚与重试；它不依赖已删除的 repositories。
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

describe("createAppDatabase", () => {
  it("creates a fresh core and memory schema without Task tables", () => {
    const root = makeTempRoot();
    const database = createAppDatabase({
      filePath: join(root, "aurabot.sqlite"),
      userDataPath: root
    });
    const tables = tableNames(database);
    const sessionSql = (
      database
        .prepare("select sql from sqlite_master where type = 'table' and name = 'sessions'")
        .get() as { sql: string }
    ).sql;

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
      expect.arrayContaining(["tasks", "task_events", "task_artifacts", "task_input_artifacts"])
    );
    expect(sessionSql).not.toContain("'task'");
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
    expect(database.prepare("select count(*) from session_links").pluck().get()).toBe(0);
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

  it("rolls back migration 8 on cleanup failure and succeeds on retry", () => {
    const root = makeTempRoot();
    const userDataPath = join(root, "locked-user-data");
    const filePath = join(root, "aurabot.sqlite");
    mkdirSync(join(userDataPath, "task-inputs"), { recursive: true });
    createLegacyDatabase(filePath);
    chmodSync(userDataPath, 0o500);

    expect(() => createAppDatabase({ filePath, userDataPath })).toThrow(/Task runtime cleanup/);

    const failedDatabase = new Database(filePath);
    expect(tableNames(failedDatabase)).toContain("tasks");
    expect(
      failedDatabase.prepare("select count(*) from schema_migrations where id = 8").pluck().get()
    ).toBe(0);
    failedDatabase.close();

    chmodSync(userDataPath, 0o700);
    const retriedDatabase = createAppDatabase({ filePath, userDataPath });
    expect(tableNames(retriedDatabase)).not.toContain("tasks");
    expect(
      retriedDatabase.prepare("select count(*) from schema_migrations where id = 8").pluck().get()
    ).toBe(1);
    retriedDatabase.close();
  });
});
