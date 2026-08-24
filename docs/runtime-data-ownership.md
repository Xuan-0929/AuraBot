# Runtime Data Ownership

## 当前所有权

| 数据 | 当前 owner | 说明 |
| --- | --- | --- |
| Electron profiles/sessions/messages | Electron main process | 保留普通主聊天数据；Session 只有 Main Session 语义。 |
| Electron memory/event logs | Electron main process | 保留 Memory 与非 task.* 事件。 |
| Python long/short-term memory | Python Memory modules | 与已删除 Task Runtime 独立。 |
| Task records/current question/events | Python Task Store | 两表独立数据库，不写 Electron SQLite。 |
| Task artifacts | 无 | 本轮仍未实现。 |
| Task child sessions/messages | 无 | migration 删除，不并入主聊天。 |
| Pi session/context | Pi，路径由 Python Manager/process 管理 | 使用 AuraBot 专属 Pi Home 与每 Task session 目录。 |

## 第一轮清理

~~~mermaid
flowchart TD
  OLDDB["旧 Electron DB"] --> KEEP["保留 Main Sessions/messages<br/>memory · non-task events"]
  OLDDB --> DROP["删除 tasks及子表<br/>task sessions · task.* events"]
  OLDFS["userData"] --> D1["删除 task-inputs"]
  OLDFS --> D2["删除 task-agent-output"]
  OLDFS --> SAFE["保留所有其他目录"]
~~~

新数据库基线不创建四张 Task 表。已有数据库保留历史 schema_migrations 版本记录，
并由 migration 8 `remove_legacy_task_runtime_data` 删除旧 Task 数据。migration 9
`canonicalize_main_session_schema` 再将升级库规范化为与新库一致的 Main Session
schema：`sessions` 不再包含 `kind`、`parent_session_id` 或 `depth`，并删除整个
`session_links` 表。

数据库迁移与磁盘清理采用真实的两阶段语义：

~~~mermaid
flowchart LR
  TX["提交 SQLite migrations 8/9"] --> CLEAN["幂等清理两个固定目录"]
  CLEAN -->|"全部成功或已不存在"| READY["应用继续启动"]
  CLEAN -->|"部分/全部失败"| BLOCK["阻止本次启动"]
  BLOCK --> RETRY["下次启动重试剩余目录"]
~~~

磁盘删除无法随 SQLite transaction 回滚：即使第二个目录删除失败，第一个目录也
不会恢复，已经提交的数据库 migration 也不会回滚。这个清理只接受
`<userData>/task-inputs` 与 `<userData>/task-agent-output` 两个直接子项，对符号链接
只删除链接本身；它必须可重复执行，并在任何剩余删除失败时阻止应用启动。

## 第二轮写入规则

- Python TaskManager/Store 是 Task 业务数据唯一 owner，数据库位于
  `<userData>/task/task-runtime.sqlite3`。
- Task SQLite 只含 `tasks` 与 `task_events`，并使用 `PRAGMA user_version=1`；当前问题
  折叠在 tasks，问题历史由 events 保存。旧 Task schema 不自动迁移。
- SQLite 的 `row_id` 是唯一 FIFO 真相；Manager 只保存最多五个 active TaskRun。
- Electron 只能消费 API/event projection，不得恢复本地 Task write path。
- Pi 拥有进程内部上下文，但不直接成为 AuraBot 产品数据库 owner。
- 本轮不向第一轮 Electron DB 添加 Task migration，也不双写 Memory DB。
