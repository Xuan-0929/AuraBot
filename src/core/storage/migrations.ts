export interface Migration {
  id: number;
  name: string;
  up: string;
  after?: (database: import("./db").AppDatabase) => void;
}

export const MIGRATIONS: Migration[] = [
  {
    id: 1,
    name: "initial_core_memory_task_schema",
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
        created_at text not null,
        updated_at text not null
      );

      create table if not exists sessions (
        id text primary key,
        profile_id text not null references profiles(id) on delete cascade,
        kind text not null check (kind in ('main', 'task')),
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

      create table if not exists tasks (
        id text primary key,
        profile_id text not null references profiles(id) on delete cascade,
        parent_session_id text not null references sessions(id) on delete cascade,
        session_id text not null references sessions(id) on delete cascade,
        task_kind text not null default 'result_output' check (
          task_kind in ('result_output', 'time_reminder')
        ),
        title text not null,
        status text not null check (
          status in ('queued', 'running', 'waiting', 'completed', 'failed', 'interrupted')
        ),
        latest_progress text not null default '',
        due_at text,
        created_at text not null,
        updated_at text not null
      );

      create table if not exists task_events (
        id text primary key,
        task_id text not null references tasks(id) on delete cascade,
        event_type text not null,
        payload_json text not null,
        created_at text not null
      );

      create table if not exists task_artifacts (
        id text primary key,
        task_id text not null references tasks(id) on delete cascade,
        artifact_type text not null,
        uri text not null,
        created_at text not null
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
    id: 2,
    name: "add_task_session_id_to_existing_task_tables",
    up: "",
    after(database) {
      const columns = database
        .prepare("pragma table_info(tasks)")
        .all()
        .map((row) => (row as { name: string }).name);

      if (!columns.includes("session_id")) {
        database.exec("alter table tasks add column session_id text");
      }
    }
  },
  {
    id: 3,
    name: "add_message_metadata_json",
    up: "",
    after(database) {
      const messageTable = database
        .prepare("select name from sqlite_master where type = 'table' and name = 'messages'")
        .get();

      if (!messageTable) {
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
      const soulTable = database
        .prepare("select name from sqlite_master where type = 'table' and name = 'souls'")
        .get();

      if (!soulTable) {
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
    id: 5,
    name: "add_task_kind_and_due_at",
    up: "",
    after(database) {
      const taskTable = database
        .prepare("select name from sqlite_master where type = 'table' and name = 'tasks'")
        .get();

      if (!taskTable) {
        return;
      }

      const columns = database
        .prepare("pragma table_info(tasks)")
        .all()
        .map((row) => (row as { name: string }).name);

      if (!columns.includes("task_kind")) {
        database.exec("alter table tasks add column task_kind text not null default 'result_output'");
      }

      if (!columns.includes("due_at")) {
        database.exec("alter table tasks add column due_at text");
      }
    }
  }
];
