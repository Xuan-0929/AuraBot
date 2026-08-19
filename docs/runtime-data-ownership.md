# Runtime Data Ownership

## 当前所有权

| 数据 | 当前 owner | 说明 |
| --- | --- | --- |
| Electron profiles/sessions/messages | Electron main process | 保留普通主聊天数据。 |
| Electron memory/event logs | Electron main process | 保留 Memory 与非 task.* 事件。 |
| Python long/short-term memory | Python Memory modules | 与已删除 Task Runtime 独立。 |
| Task records/artifacts/events | 无 | 旧 SQLite 表已清理，新 owner 尚未实现。 |
| Task child sessions/messages | 无 | migration 删除，不并入主聊天。 |
| Pi state | 无 | PI_CODING_AGENT_DIR 生命周期尚未实现。 |

## 第一轮清理

~~~mermaid
flowchart TD
  OLDDB["旧 Electron DB"] --> KEEP["保留 main sessions/messages<br/>memory · non-task events"]
  OLDDB --> DROP["删除 tasks及子表<br/>task sessions · task.* events"]
  OLDFS["userData"] --> D1["删除 task-inputs"]
  OLDFS --> D2["删除 task-agent-output"]
  OLDFS --> SAFE["保留所有其他目录"]
~~~

新数据库基线不创建四张 Task 表。已有数据库保留历史 schema_migrations 版本记录，
并由 remove_legacy_task_runtime_data migration 执行清理。

## 未来切换规则

- Python TaskManager 将成为 Task 业务数据唯一 owner。
- Electron 只能消费 API/event projection，不得恢复本地 Task write path。
- Pi 拥有进程内部上下文，但不直接成为 AuraBot 产品数据库 owner。
- owner 切换必须有明确 migration、回退边界和端到端验证，禁止双写。
