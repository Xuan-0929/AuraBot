# Pi Task Runtime

> 本文是 Task 状态机与 Pi 执行边界的事实源。Python TaskManager、Main Agent 工具与 Electron 只读列表已接通；Artifact 与生产运行时打包仍在本轮范围之外。桌面配置及接入契约见 [系统模块](../modules-v2.md) 和 [后端契约](../backend-runtime-contract.md)。

## 1. 架构与范围

Task runtime 是本地、单进程、单事件循环、单用户系统。`TaskManager` 是运行状态的唯一
owner；SQLite 是队列和恢复事实源，不存在第二份内存 queue、Scheduler 或 Runtime Engine。

~~~mermaid
flowchart LR
  MA["Main Agent / TaskOperations"] --> TM["TaskManager<br/>唯一运行状态 owner"]
  TM --> GW["FastAPI Gateway<br/>只读 task.snapshot SSE"]
  GW --> CLIENT["Electron Task 列表"]
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
内部业务事件、只读状态快照 SSE、累计 active timeout、fail-stop、重启与 shutdown 收口。恢复复用
Pi session，但不恢复进程调用栈，也不保证 Tool 外部副作用 exactly-once。

## 2. 模块与依赖

~~~text
backend/task/
  __init__.py   # Main Agent 与 Gateway 使用的公共模型/Manager
  manager.py    # 命令、dispatcher、TaskRun、execution、recovery、fail-stop、SSE wake
  store.py      # 同步 sqlite3、两表 schema、CAS、事件与 FIFO claim
  process.py    # start_pi / stop_pi / remove_session
  rpc.py        # JSONL framing、response correlation、有界事件与 Pi payload 归一化
  models.py     # 七状态产品模型与纯转换规则
  settings.py   # 路径、timeout 与 pinned runtime 验证
~~~

Main Agent 通过 TaskBridge 调用 `backend.task` 的 Manager 和公共模型；Gateway 只订阅状态快照。Manager 自己构造 Store，并直接拥有
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
`task_sequence` 在单 Task 内唯一递增，内部 Main Agent 使用 watermark 追踪；桌面 SSE 只发当前状态。

当前 schema 使用 `PRAGMA user_version=3`。空库直接建 `tasks` 与 `task_events`，原生
CHECK/FK/UNIQUE、事务与 CAS 负责数据一致性；当前版本可重新打开，旧版、未知版拒绝且不重建。
不支持未发布版本迁移，也不维护第二套 DDL 契约或自研 SQL 解析器。启动不做全库外键扫描。
`tasks_owner_recent_idx` 支持 Profile 的最新状态查询；旧数据按已批准范围一次性重置。

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
  清理失败向 Main Agent 报错，重复删除工具调用或下次 startup 重试。session 路径本身是 symlink 时只
  unlink，不跟随目标。

## 9. Main Agent 与只读状态 SSE

Main Agent 是 TaskManager 唯一业务主入口，使用进程内方法完成创建、查询、回答、暂停、恢复、
取消、终态删除和重跑。TaskOperations 持有提案和已接管控制：每提案一个 worker，三秒内
可冻结/恢复/取消，提交后不因调用者取消而撤销。Context 投影失败报告一次，持久 Task 不回滚。

唯一 Task HTTP 出口是 `GET /api/v1/task-events?ownerProfileId=…`：首次/重连立即发送
`event: task.snapshot` 与 `data: {"tasks":[{"id":"…","title":"…","status":"…"}]}`。
只含最近创建的 100 个未删除 Task，降序。创建、状态变化、删除通知 snapshot condition；
输出不会唤醒状态读取。同内容快照不重复发送；空闲保活，断开取消等待。无 REST 控制/查询、
执行正文、事件 ID 或 Last-Event-ID replay。

完整 `task.created/status_changed/output_appended/question_raised/question_answered/deleted`
事件保留在 TaskStore。Main Agent 启动先取 watermark，再分批对齐当前 Profile 的记录（含
删除事实），随后消费较新的重要事件。`ContextStore.upsert_task_fact` 按稳定 ID/version 去重；
无需 Runtime 版本缓存或逐事件展开全 Context。`GET /health` 仍返回
`{"ok":true,"service":"backend"}`。

## 10. 验证边界

测试聚焦 models、sync Store、RPC、process、Manager、Gateway/SSE 和真实 pinned Pi offline
smoke，不镜像已删除的内部包。默认测试不调用模型、不需要 provider key、不访问网络。

当前交付口径：**TaskManager 与 Main Agent 工具已接通，Electron 只读展示当前 Profile 的 Task 标题/状态；Artifact 与生产发行打包尚未完成。**

## 桌面接入补充（2026-09-05）

- Electron 只订阅 Task 状态快照 SSE，不提供创建、暂停、取消、删除等控制按钮。
- Main Agent 负责创建、查询、问题回答、暂停、恢复、取消、终态删除和重跑；重跑通过正常 create 路径生成新 Task，并保留来源关联。
- 状态流要求 `ownerProfileId`；Main Agent 工具对每次操作重新检查 Profile 所有权。Task REST 已删除，Memory 不提供外部管理接口。
- 模型和工作目录由 Electron settings.json 配置，Python 仅启动时读取。Pi provider 配置写在 AuraBot 专属数据根的 `pi-agent`，不读取用户全局 Pi home。
- 未配置 Pi 能力仍能启动桌面/Backend 并读取 Task；创建/恢复等需要执行的操作明确不可用。
- Apply 的 restart prepare 在检查忙碌状态后阻止新 Task 变更与派发；退出旧 Backend 后才启动新进程。
