# Pi Task Runtime

> 本文是 AuraBot Task Runtime 的唯一架构事实源。Python Backend 垂直切片已实现；
> Electron、Main Agent、Artifact 与生产发行打包仍未接入。

## 1. 架构与范围

Task runtime 是本地、单进程、单事件循环、单用户系统。`TaskManager` 是运行状态的唯一
owner；SQLite 是队列和恢复事实源，不存在第二份内存 queue、Scheduler 或 Runtime Engine。

~~~mermaid
flowchart LR
  CLIENT["HTTP Client"] --> GW["FastAPI Gateway<br/>REST / SSE"]
  GW --> TM["TaskManager<br/>唯一运行状态 owner"]
  TM --> DB["TaskStore<br/>同步 sqlite3"]
  TM --> RUNS["最多 5 个 TaskRun"]
  RUNS --> PROC["process.py<br/>Pi 启停 / reap / session 清理"]
  PROC --> RPC["rpc.py<br/>严格 JSONL"]
  RPC --> PI["Pinned Pi 0.84.2"]
  PI --> EXT["@scpz24/pi-aurabot-task"]
  DB --> T["tasks"]
  DB --> E["task_events"]
~~~

已实现创建、查询、SQLite FIFO、固定并发、暂停、恢复、取消、逻辑删除、问题回答、
业务事件、SSE replay、累计 active timeout、fail-stop、重启与 shutdown 收口。恢复复用
Pi session，但不恢复进程调用栈，也不保证 Tool 外部副作用 exactly-once。

## 2. 模块与依赖

~~~text
backend/task/
  __init__.py   # Gateway 的稳定公开边界
  manager.py    # 命令、dispatcher、TaskRun、execution、recovery、fail-stop、SSE wake
  store.py      # 同步 sqlite3、两表 schema、CAS、事件与 FIFO claim
  process.py    # start_pi / stop_pi / remove_session
  rpc.py        # JSONL framing、response correlation、有界事件与 Pi payload 归一化
  models.py     # 七状态产品模型与纯转换规则
  settings.py   # 路径、timeout 与 pinned runtime 验证
~~~

Gateway 只依赖 `backend.task` 的 Manager 和公共模型。Manager 自己构造 Store，并直接拥有
Pi handle；Store、process 与 RPC 不依赖 Gateway。没有 interface、factory、repository、
unit-of-work、兼容 adapter 或 deprecated alias。

Manager 的易失状态只有一个 dispatcher、一个唤醒 Event、一个全局状态锁、最多五项的
`dict[UUID, TaskRun]`、SSE condition/revision 和 lifecycle/fail-stop 标志。TaskRun 只保存
task id、worker、Pi handle、累计 active time 与 monotonic 起点，结束立即删除。

## 3. 七状态产品模型

~~~mermaid
stateDiagram-v2
  [*] --> queued
  queued --> running: dispatcher claim
  queued --> paused
  queued --> cancelled

  running --> waiting: 问题 durable 后立即 reap Pi
  running --> completed
  running --> failed
  running --> paused: reap 后提交
  running --> cancelled: reap 后提交

  waiting --> queued: 答案 durable
  waiting --> paused
  waiting --> cancelled

  paused --> queued: 无待答问题或答案已保存
  paused --> waiting: 仍有未答问题
~~~

公开状态固定为 `queued`、`running`、`waiting`、`paused`、`completed`、`failed`、
`cancelled`。`running` 同时覆盖 Pi 启动和执行；pause/cancel 期间数据库继续显示原状态，
只在子进程完整 reap 后提交最终状态。命令尚未返回时 Backend 崩溃，命令视为未提交，
重启按普通 running recovery 处理，不持久化私有 stop intent。

## 4. SQLite FIFO 与固定并发

最大 active Task 固定为 5，不可配置，也不预启动 Pi。实测五个空闲 Pi 0.84.2 约
656 MiB RSS；当前用户 P95 并发不超过 2。只有实测资源压力证明固定上限不合适时，
才考虑自适应 admission。

~~~mermaid
sequenceDiagram
  participant D as Dispatcher
  participant S as TaskStore
  participant R as TaskRun
  D->>S: claim_queued(5 - len(runs))
  S->>S: BEGIN IMMEDIATE
  S->>S: oldest row_id: queued → running + version + event
  S-->>D: claimed TaskRecord[]
  D->>R: one worker per claimed Task
  R-->>D: finally remove run + wake
~~~

没有排队 Task 时，空闲 Manager 只有 dispatcher。每个 active Task 只有一个 worker 和
一个 RPC stdout reader。全局锁允许最多五个 teardown 短暂串行；只有观测到不可接受的
命令延迟时才升级为 run-local locks。

## 5. 两表同步 Store

数据库位于 `<userData>/task/task-runtime.sqlite3`。Store 使用标准库 `sqlite3`、单连接、
WAL、foreign keys 和 busy timeout；一次同步 transaction 内没有 `await`，所以不需要
SQLite worker thread 或 write lock。

~~~mermaid
erDiagram
  TASKS ||--o{ TASK_EVENTS : emits
  TASKS {
    integer row_id PK
    text id UK
    text status
    integer active_runtime_ms
    integer version
    text question_id
    text question_text
    text question_options_json
    text question_answer
    text deleted_at
  }
  TASK_EVENTS {
    integer event_id PK
    text task_id FK
    integer task_sequence
    text event_type
    text payload_json
  }
~~~

问题当前值折叠在 tasks 行；完整问题历史由 durable events 保存。所有状态写入、CAS
version 与业务事件共享 `BEGIN IMMEDIATE` transaction。`event_id` 全局递增，
`task_sequence` 在单 Task 内唯一递增，SSE 直接按 cursor replay。

新模型使用 `PRAGMA user_version=2`。空数据库只创建 `tasks` 和 `task_events`；启动时仅将
首个未发布版本的 v1 原地补充 `workspace_id`、`owner_profile_id` 与 `idempotency_key`，既有行
标记为 `legacy` 且没有可复用的幂等键。发现旧表、未知版本或未来版本时拒绝启动，不自动删除、
改名或迁移。用户必须先备份，再删除整个 `<userData>/task` 目录，避免 SQLite 与旧 session
失去关联。

## 6. 问题、执行与 active timeout

收到 AuraBot `ask_main_agent` 问题后，Manager 原子写入当前问题和 `waiting`，随后立即
abort/关闭/reap Pi 并释放运行槽。没有 60 秒 timer、hibernate registry、live answer
delivery 或 stderr drainer。第三方 Extension dialog 立即发送 cancelled response。

回答只接受 waiting Task 的当前问题；答案、`task.question_answered` 与 `waiting → queued`
在同一 transaction 提交。新 Pi 复用同一 session，并通过 continuation prompt 获得持久化
答案；prompt accepted 后清空当前问题字段。若清空写入失败，Manager 进入 fail-stop；答案
仍在数据库中，因此恢复时至多重放。

active timeout 从 claim 开始累计，Pi 启动和执行计时；queued、waiting 和 paused 不计时。
暂停或等待保存累计毫秒，恢复后继续；没有 Task 级自动重试。

## 7. Process 与 RPC 边界

生产 subprocess 的 stderr 直接使用 `DEVNULL`，避免每 Pi 一个 Python drainer 和 64 KiB
tail。真实离线 smoke 可单独使用 PIPE 捕获启动诊断。停止顺序固定为 abort、关闭 stdin、
等待、terminate process group、kill process tree、`wait()` reap；`stop_pi` 只在已 reap 后
返回。completed 使用 `abort=False`，其他退出默认 abort。

RPC 保留 LF JSONL、严格 UTF-8/object/type/最大行、command timeout、response id 关联、
unknown/duplicate response 拒绝、fatal 广播、有界事件队列、写锁、text delta 合并、
Unicode 安全切块、事件顺序、EOF pending 检查和未知合法 event 忽略。RPC 不认识 Task 状态。

## 8. 恢复、shutdown、fail-stop 与删除

- startup：清理已 tombstone Task 的残留 session；queued/waiting/paused 保持；running 有
  session 时变为 `paused(backend_restarted)`，缺失时变为 `failed(session_missing)`。
- shutdown：停止 admission，最多五个 worker 并行回收；仍 running 的 Task 在 reap 后变为
  `paused(backend_shutdown)`；waiting 保持。
- fail-stop：任一 durable write 失败后停止 admission、取消 dispatcher、并行回收所有 Pi；
  查询仍可用于诊断，但新的状态命令返回 runtime unavailable。
- delete：只允许 terminal Task；先提交 tombstone 和 `task.deleted`，再直接清理 session。
  清理失败返回 500，重复 DELETE 或下次 startup 重试。session 路径本身是 symlink 时只
  unlink，不跟随目标。

## 9. Gateway 与事件

| Method | Path | 行为 |
| --- | --- | --- |
| POST | `/api/v1/tasks` | 创建 queued Task |
| GET | `/api/v1/tasks` | cursor 分页和状态过滤 |
| GET | `/api/v1/tasks/{taskId}` | 查询 snapshot |
| POST | `/api/v1/tasks/{taskId}/pause` | CAS 暂停 |
| POST | `/api/v1/tasks/{taskId}/resume` | CAS 恢复 |
| POST | `/api/v1/tasks/{taskId}/cancel` | CAS 取消 |
| DELETE | `/api/v1/tasks/{taskId}` | terminal tombstone + session cleanup |
| GET | `/api/v1/task-events` | Last-Event-ID SSE replay |

公共 JSON 保持 camelCase，Task response 不暴露 Pi/session/question 内部字段。业务事件保持
`task.created`、`task.status_changed`、`task.output_appended`、`task.question_raised`、
`task.question_answered`、`task.deleted`。SSE 内存 condition 只负责无轮询唤醒；持久化
事件是唯一 replay 真相。`GET /health` 仍精确返回 `{"ok":true,"service":"backend"}`。

## 10. 验证边界

测试聚焦 models、sync Store、RPC、process、Manager、Gateway/SSE 和真实 pinned Pi offline
smoke，不镜像已删除的内部包。默认测试不调用模型、不需要 provider key、不访问网络。

最终交付口径：**Python Pi Task 后端垂直切片已实现；Electron、Main Agent、Artifact 与
发行打包尚未接入。**
