/**
 * 定义 Electron SQLite 的 core/Memory schema 与旧 Task Runtime 清理迁移。
 *
 * 新库只创建非 Task 业务表；已有库在同一数据库事务中删除旧 Task 表、子会话与
 * task.* 事件，并通过受控 userData context 清理两个精确目录。
 */

import { lstatSync, realpathSync, rmSync, unlinkSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import type { AppDatabase } from "./db";

export interface MigrationContext {
  userDataPath: string;
}

export interface Migration {
  id: number;
  name: string;
  up: string;
  after?: (database: AppDatabase, context: MigrationContext) => void;
}

const LEGACY_TASK_DIRECTORIES = ["task-inputs", "task-agent-output"] as const;

export const MIGRATIONS: Migration[] = [
  {
    id: 1,
    name: "initial_core_memory_schema",
    up: `
      create table if not exists profiles (
        id text primary key,
        display_name text not null,
        active integer not null default 0,
        created_at text not null,
        updated_at text not null
      );

      create table if not exists souls (
        id text primary key,
        profile_id text not null unique references profiles(id) on delete cascade,
        name text not null,
        persona text not null,
        tone text not null default '',
        principles_json text not null default '[]',
        taboos_json text not null default '[]',
        speaking_style text not null,
        relationship text not null,
        boundaries_json text not null default '[]',
        topic_preferences_json text not null default '[]',
        proactive_level text not null default 'balanced',
        reminder_style text not null default '',
        reply_preference text not null default '',
        created_at text not null,
        updated_at text not null
      );

      create table if not exists sessions (
        id text primary key,
        profile_id text not null references profiles(id) on delete cascade,
        kind text not null check (kind = 'main'),
        title text not null,
        parent_session_id text references sessions(id) on delete set null,
        depth integer not null default 0,
        created_at text not null,
        updated_at text not null
      );

      create table if not exists session_links (
        id text primary key,
        parent_session_id text not null references sessions(id) on delete cascade,
        child_session_id text not null references sessions(id) on delete cascade,
        relation_type text not null,
        created_at text not null,
        unique(parent_session_id, child_session_id, relation_type)
      );

      create table if not exists messages (
        id text primary key,
        session_id text not null references sessions(id) on delete cascade,
        profile_id text not null references profiles(id) on delete cascade,
        role text not null check (role in ('user', 'assistant', 'system', 'tool')),
        content text not null,
        metadata_json text not null default '{}',
        created_at text not null
      );

      create index if not exists idx_messages_session_created
        on messages(session_id, created_at);

      create table if not exists memory_nodes (
        id text primary key,
        profile_id text not null references profiles(id) on delete cascade,
        type text not null check (type in ('trait', 'preference', 'event', 'follow_up')),
        state text not null check (state in ('active', 'stale', 'completed', 'archived')),
        content text not null,
        importance real not null check (importance >= 0 and importance <= 1),
        starts_at text,
        ends_at text,
        created_at text not null,
        updated_at text not null
      );

      create index if not exists idx_memory_nodes_profile_state
        on memory_nodes(profile_id, state, type);

      create table if not exists memory_edges (
        id text primary key,
        profile_id text not null references profiles(id) on delete cascade,
        source_node_id text not null references memory_nodes(id) on delete cascade,
        target_node_id text not null references memory_nodes(id) on delete cascade,
        relation_type text not null check (
          relation_type in ('relates_to', 'caused_by', 'scheduled_for', 'preference_for', 'opposes')
        ),
        weight real not null check (weight >= 0 and weight <= 1),
        created_at text not null,
        unique(source_node_id, target_node_id, relation_type)
      );

      create index if not exists idx_memory_edges_profile_source
        on memory_edges(profile_id, source_node_id);

      create table if not exists memory_summaries (
        id text primary key,
        profile_id text not null references profiles(id) on delete cascade,
        topic text not null,
        content text not null,
        updated_at text not null,
        unique(profile_id, topic)
      );

      create table if not exists memory_write_queue (
        id text primary key,
        profile_id text not null references profiles(id) on delete cascade,
        source_session_id text not null references sessions(id) on delete cascade,
        payload_json text not null,
        status text not null check (status in ('queued', 'running', 'completed', 'failed')),
        created_at text not null,
        updated_at text not null
      );

      create table if not exists event_logs (
        id text primary key,
        event_type text not null,
        payload_json text not null,
        created_at text not null
      );
    `
  },
  {
    id: 3,
    name: "add_message_metadata_json",
    up: "",
    after(database) {
      const table = database
        .prepare("select name from sqlite_master where type = 'table' and name = 'messages'")
        .get();
      if (!table) {
        return;
      }
      const columns = database
        .prepare("pragma table_info(messages)")
        .all()
        .map((row) => (row as { name: string }).name);
      if (!columns.includes("metadata_json")) {
        database.exec("alter table messages add column metadata_json text not null default '{}'");
      }
    }
  },
  {
    id: 4,
    name: "add_soul_voice_fields",
    up: "",
    after(database) {
      const table = database
        .prepare("select name from sqlite_master where type = 'table' and name = 'souls'")
        .get();
      if (!table) {
        return;
      }
      const columns = database
        .prepare("pragma table_info(souls)")
        .all()
        .map((row) => (row as { name: string }).name);
      if (!columns.includes("tone")) {
        database.exec("alter table souls add column tone text not null default ''");
      }
      if (!columns.includes("principles_json")) {
        database.exec("alter table souls add column principles_json text not null default '[]'");
      }
      if (!columns.includes("taboos_json")) {
        database.exec("alter table souls add column taboos_json text not null default '[]'");
      }
    }
  },
  {
    id: 6,
    name: "add_persona_settings_fields_to_souls",
    up: "",
    after(database) {
      const table = database
        .prepare("select name from sqlite_master where type = 'table' and name = 'souls'")
        .get();
      if (!table) {
        return;
      }
      const columns = database
        .prepare("pragma table_info(souls)")
        .all()
        .map((row) => (row as { name: string }).name);
      if (!columns.includes("proactive_level")) {
        database.exec("alter table souls add column proactive_level text not null default 'balanced'");
      }
      if (!columns.includes("reminder_style")) {
        database.exec("alter table souls add column reminder_style text not null default ''");
      }
      if (!columns.includes("reply_preference")) {
        database.exec("alter table souls add column reply_preference text not null default ''");
      }
    }
  },
  {
    id: 8,
    name: "remove_legacy_task_runtime_data",
    up: `
      drop table if exists task_input_artifacts;
      drop table if exists task_artifacts;
      drop table if exists task_events;
      drop table if exists tasks;
      delete from sessions where kind = 'task';
      delete from event_logs where event_type like 'task.%';
    `,
    after(database, context) {
      cleanupLegacyTaskDirectories(context.userDataPath);
      const violations = database.pragma("foreign_key_check") as unknown[];
      if (violations.length > 0) {
        throw new Error("Task runtime cleanup failed: foreign key violations remain");
      }
    }
  }
];

function cleanupLegacyTaskDirectories(userDataPath: string): void {
  const canonicalRoot = realpathSync(resolve(userDataPath));

  for (const directoryName of LEGACY_TASK_DIRECTORIES) {
    const candidate = resolve(join(canonicalRoot, directoryName));
    if (
      dirname(candidate) !== canonicalRoot ||
      basename(candidate) !== directoryName
    ) {
      throw new Error("Task runtime cleanup rejected an unsafe path");
    }

    try {
      const stat = lstatSync(candidate);
      if (stat.isSymbolicLink()) {
        unlinkSync(candidate);
      } else {
        rmSync(candidate, { force: true, recursive: true });
      }
    } catch (error) {
      if (isMissingPathError(error)) {
        continue;
      }
      const detail = error instanceof Error ? error.message : String(error);
      throw new Error(
        `Task runtime cleanup failed for ${directoryName}: ${detail}`,
        { cause: error }
      );
    }
  }
}

function isMissingPathError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    error.code === "ENOENT"
  );
}
