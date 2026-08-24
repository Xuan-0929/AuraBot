# Pi Task Runtime

> 设计状态：第二轮设计已批准，本文是 AuraBot Task Runtime 的唯一架构事实源。
> 实现状态：Python 后端垂直切片已实现；Electron、Main Agent、Artifact 与生产
> 发行打包仍未接入。

## 1. 范围与验收

第二轮在既有 `backend/main.py → FastAPI → backend/app/gateway` 机架中新增
Python Control Plane，并以项目精确锁定的 Pi CLI 作为 Execution Plane。

~~~mermaid
flowchart LR
  CLIENT["HTTP Client"] --> GW["REST + SSE Gateway"]
  GW --> TM["TaskManager 门面"]
  TM --> DB["TaskStore façade"]
  TM --> RT["TaskRuntimeEngine"]
  TM --> DEL["TaskSessionDeletion"]
  RT --> Q["FIFO Scheduler"]
  RT --> SUP["Pi Process Supervisor"]
  RT --> EX["execution.run_task"]
  DB --> SQL["schema / questions / events / codec"]
  SUP --> RPC["stdin/stdout JSONL"]
  RPC --> PI["Pinned Pi 0.84.2"]
  PI --> EXT["@scpz24/pi-aurabot-task"]
~~~

本轮验收包含 Task 创建、查询、FIFO 排队、并发控制、暂停、session 级恢复、
取消、逻辑删除、业务事件、SSE、`ask_main_agent` 等待、超时、进程故障、重启
与 Backend shutdown 收口。恢复只复用 Pi session 上下文，不恢复进程调用栈，
也不承诺 Tool 外部副作用 exactly-once。

本轮不实现 Electron Task UI/IPC、Main Agent 真实接线、Artifact、Task Profile、
Package 管理 UI、生产安装包、远程 Worker、true resume、自研权限/sandbox、MCP
Server 或 Node SDK Worker。

## 2. 目录结构与依赖方向

~~~text
backend/
  main.py                       # 组合入口与 lifespan
  app/gateway/
    task_schemas.py             # 公共 HTTP DTO
    task_routes.py              # REST/SSE 映射
  task/
    __init__.py                 # Gateway/Main Agent 稳定公开入口
    manager.py                  # 跨 Store/Runtime 的业务命令门面
    models.py                   # 产品模型与纯状态转换
    settings.py                 # 路径、版本与运行参数
    runtime/
      engine.py                 # 调度、锁、clock、recovery 与 fail-stop
      execution.py              # 单 Task Pi 执行主协程
      questions.py              # Question/continuation 纯协议映射
      scheduler.py              # FIFO 与并发槽
      process.py                # 唯一 subprocess owner
      rpc.py                    # 严格 JSONL 协议子集
    persistence/
      store.py                  # 单连接、单写锁 SQLite façade
      schema.py                 # v2 DDL、migration 与验证
      questions.py              # Question 跨表事务 helper
      events.py                 # Event 写入、sequence 与 replay
      codec.py                  # SQLite row/time/JSON 纯转换
      sessions.py               # tombstone/session quarantine 协调
pi-runtime/                     # 私有、精确锁定的 Pi CLI 依赖载体
pi-aurabot-task/                # Pi-native Extension/Prompt Package
~~~

依赖保持单向：Gateway 只从 `backend.task` 调用 TaskManager；Manager 只编排 Store、
Runtime Engine 与 session deletion；Engine 拥有全部进程内运行状态并调用单 Task 主协程；
Store 仍独占唯一 SQLite connection、write lock 和 transaction runner。Persistence 不
依赖 Runtime/Manager，Runtime 不依赖 Manager/Gateway。Supervisor 独占 subprocess
handle；RPC 不认识产品状态；Scheduler 不访问 SQLite 或进程。Pi Package 不依赖 Python
业务模型。第一版不创建 Repository interface、UnitOfWork、per-Task Execution 对象、
application-service 森林或 AuraBot-to-Pi Tool/Skill 转换层。

## 3. 产品模型与状态

~~~mermaid
stateDiagram-v2
  [*] --> queued
  queued --> starting
  queued --> paused
  queued --> cancelled
  starting --> running
  starting --> pausing
  starting --> cancelling
  starting --> failed
  running --> waiting
  running --> pausing
  running --> cancelling
  running --> completed
  running --> failed
  waiting --> running
  waiting --> pausing
  waiting --> cancelling
  waiting --> queued: hibernate 后得到回答
  pausing --> paused
  cancelling --> cancelled
  paused --> queued
  paused --> waiting: 尚有未答问题
~~~

状态固定为 `queued`、`starting`、`running`、`waiting`、`pausing`、`paused`、
`cancelling`、`completed`、`failed`、`cancelled`。timeout 是 `failed` 的错误
类别。重跑创建新 Task，并以 `rerunOfTaskId` 关联；第一版不拆 ExecutionAttempt。

Task 产品记录从创建保留到终态和逻辑删除。`paused` 不持有进程且可显式恢复；
`waiting` 表示 `ask_main_agent` 尚未回答，或回答刚持久化而 delivery recovery 尚未
排队。删除只允许终态 Task：数据库保留记录、问题与事件，普通查询隐藏该 Task；
重复 DELETE 幂等，不增加 version，也不重复产生 `task.deleted`。

## 4. Process Supervisor 与 Pi RPC

每个运行中的 Task 对应一个独立 Pi RPC 进程。开发启动命令使用显式配置的 Node
和绝对 Pi CLI 路径，不调用全局 `pi`、`npx` 或未知 PATH 版本：

~~~text
<node> <absolute pi cli>
  --mode rpc
  --session-dir <userData>/task/sessions/<task-id>
  --session-id <task-id>
  --name <title>
  --no-approve
  --extension <absolute aurabot-task/index.ts>
  --append-system-prompt <absolute task-system.md>
~~~

Pi 环境固定注入 `PI_CODING_AGENT_DIR=<userData>/pi-agent`、
`PI_SKIP_VERSION_CHECK=1` 与 `PI_TELEMETRY=0`。Provider、模型、认证和用户启用的
Pi-native 资源来自这个隔离 Home；`--no-approve` 避免自动加载未信任的项目本地
`.pi` 资源。

RPC 采用严格 LF JSONL。Python 只解析本轮需要的 command/response/event 子集；
response 以 command id 关联，`prompt` response 仅表示 accepted，`agent_settled`
才是完成点，`agent_end` 不能使 Task 完成。非法 UTF-8、非对象、缺失 type、未知或
重复 response id、超限行和不完整 EOF 都收口为 `protocol_error`。合法未知 event
只记录并忽略。stdout 进入有界协议队列，stderr 独立 drain，SSE 只读数据库事件。

停止顺序为 RPC abort、等待 settled/response、关闭 stdin、等待退出、终止进程组、
强杀进程树并 `wait()` reap。只有进程已 reap 后，Task 才能提交 paused 或终态。
无法回收时停止 admission，保留 `pausing/cancelling` 并记录 critical。

## 5. Persistence

Python 是 Task 数据唯一 owner，数据库位于
`<userData>/task/task-runtime.sqlite3`，与 Electron Chat/Memory SQLite 及 Python
Memory 存储分离。表固定为 `schema_migrations`、`tasks`、`task_events` 和
`task_questions`。当前 Task schema 版本为 v2；`task_questions.answered_at` 表示
AuraBot 已接受并持久化回答，`delivered_at` 表示回答已写入 live Pi，或携带回答的
continuation prompt 已被新 Pi 接受。`task.question_answered` 只表达前一种事实。

- 状态写入与业务事件在同一 SQLite transaction。
- `version` 使用 compare-and-swap。
- `event_id` 全局递增；`task_sequence` 在单 Task 内严格递增。
- 公共 payload 不保存 Pi raw object、Tool 参数/结果、thinking、stderr 或 RPC id。
- WAL、foreign keys、busy timeout 与 `foreign_key_check` 在初始化时启用。
- 进程句柄、pending RPC future、内存 notifier 和并发槽只存在内存中；恢复所需的
  Question answer 不保存在内存 continuation map。

启动 reconciliation：`queued` 按 `row_id` 重新排队；有 session 的
`starting/running/pausing` 变为 `paused(backend_restarted)`，缺少 session 则
`failed(session_missing)`；`waiting + unanswered` 原样保留，`waiting + answered`
修复为 `queued`；`cancelling` 变为 `cancelled`。queued/paused 的 continuation
完全从 SQLite session 与未投递回答派生。非终态 v1 answered Question 升级后允许
至多重放一次；Pi Tool 的外部副作用仍不保证 exactly-once。

session 删除使用 `<sessionRoot>/.deleting/<task-id>` 隔离区：先原子 rename，再在
同一数据库 transaction 写 tombstone 与 `task.deleted`，最后永久清理。DB 失败时
恢复 canonical session；DB 已提交但最终清理失败时保留隔离目录并返回 500，后续
相同 DELETE 或下次启动继续清理。只有 canonical 与隔离目录都不存在才返回 204。

~~~mermaid
flowchart LR
  C[canonical session] -->|atomic rename| Q[.deleting/task-id]
  Q -->|DB rollback| C
  Q -->|tombstone committed| P[purge]
  START[startup] -->|live DB row| C
  START -->|deleted DB row| P
  BOTH[canonical + quarantine] --> FAIL[fail closed]
~~~

非 UUID、非 canonical UUID 或数据库未知的隔离项只记录诊断，不触碰；symlink 只移动
或 unlink 链接本身，不跟随其目标。

## 6. Gateway API 与业务事件

公开 API 只投影 AuraBot 业务模型：

| Method | Path | 行为 |
| --- | --- | --- |
| POST | `/api/v1/tasks` | 创建并入队 |
| GET | `/api/v1/tasks` | cursor 分页，可按状态过滤 |
| GET | `/api/v1/tasks/{taskId}` | 查询 snapshot |
| POST | `/api/v1/tasks/{taskId}/pause` | 暂停 |
| POST | `/api/v1/tasks/{taskId}/resume` | 恢复 |
| POST | `/api/v1/tasks/{taskId}/cancel` | 永久取消 |
| DELETE | `/api/v1/tasks/{taskId}` | 终态逻辑删除并清理 session |
| GET | `/api/v1/task-events` | SSE 事件流 |

公共 JSON 使用 camelCase。pause/resume/cancel 以 `expectedVersion` 做乐观并发，
过期返回 `409 task_version_conflict`；已经达到目标状态的重复命令返回当前 snapshot。
本轮不公开问题回答 HTTP endpoint，Main Agent 将来通过 TaskManager 的内部稳定方法回答。
创建请求的 `timeoutSeconds` 可省略，省略时由
`task.default_timeout_seconds` 提供唯一默认值；显式 `null` 或超出 `1..86400`
返回 FastAPI 422。Task response 不包含尚未实现的 `pendingQuestion`。

业务事件为 `task.created`、`task.status_changed`、`task.output_appended`、
`task.question_raised`、`task.question_answered`、`task.deleted`。SSE 使用全局整数
`eventId` 与 `Last-Event-ID` replay，内存 notifier 只负责唤醒，每 15 秒发送
keepalive comment；慢客户端只落后自己的数据库 cursor。

`GET /health` 始终精确返回 `{"ok":true,"service":"backend"}`，不暴露 Task
数量或 Pi 原始健康字段。第一版仅绑定 loopback，无 CORS、远程认证或远程访问承诺。

## 7. Pi dependency、Home 与资源

`pi-runtime` 是 `private: true` 的依赖载体，精确锁定
`@earendil-works/pi-coding-agent@0.84.2`，并以本地 file dependency 安装
`@scpz24/pi-aurabot-task`。它不是 Worker、RPC Client 或 HTTP 服务。

`@scpz24/pi-aurabot-task` 是标准 Pi Package，只注册 `ask_main_agent` Tool，并携带
Task system prompt。Tool 使用固定 dialog marker `AURABOT_TASK_QUESTION_V1\n`；
Python 只把该 marker 映射为业务问题，其他第三方 dialog 立即取消并记录资源告警。

Extension、Skill、Prompt 与 Package 完全沿用 Pi 原生格式及 npm/Git/local 来源。
AuraBot 不实现 ToolGroup、SkillGroup、Capability、Package registry、MCP wrapper、
权限审批、sandbox、签名或第二套 Package Manager。Package 以运行 AuraBot 的用户
权限执行，来源和安装风险由用户负责。本轮没有安装/启用/更新 UI，也不发布 npm 包。

## 8. 错误、等待、shutdown 与跨平台

公开错误码固定为 `spawn_failed`、`startup_timeout`、`prompt_rejected`、
`resource_load_failed`、`protocol_error`、`unexpected_exit`、`model_error`、
`timeout`、`session_missing`、`result_unavailable`、`internal_error`。公开响应只含
稳定 code 与脱敏 message；exit code、signal、stderr tail 和 stack trace 只进入日志。
Task Gateway 只映射明确的 request/command、not-found、state/version conflict 与
runtime-unavailable 错误。其他 Task 或非 Task 异常统一由 Backend 边界记录 stack，
并返回 `500 internal_error` / `Backend request failed internally.`。

`ask_main_agent` 在 60 秒内等待时占用 worker 槽；回答先持久化，live write 成功后再
原子写 `delivered_at` 并恢复 `running`。live write 失败会回收旧 Pi、取消旧 execution，
将 Task 排回 `queued`，回答保持未投递。
超过 60 秒则 abort、退出并 reap，但状态保持 `waiting`、释放槽；稍后回答先持久化，
再以同一 Pi session 新进程和持久化 continuation prompt 继续；新 Pi 接受 prompt 后
才写 `delivered_at`。问题回答与休眠使用单 Task 锁及 version 串行。

Backend shutdown 总 deadline 30 秒：queued 保留，active Task 收口为
`paused(backend_shutdown)`，waiting 保持且休眠 worker，cancelling 收口为 cancelled。
所有 worker 并行停止。POSIX 使用进程组 terminate/kill；Windows 使用进程组与
`taskkill /T /F`。生产 Node/Python/Pi 打包仍延期，不能因开发期 Node PATH 支持而
宣称发行闭环完成。

## 9. 测试策略

测试目录镜像 `runtime/` 与 `persistence/`，分层覆盖纯状态机、schema migration、
Question/Event/session 事务、FIFO/并发、严格 JSONL、fake Pi process、execution/Engine、
TaskManager 生命周期、Gateway/SSE 与真实 pinned Pi offline smoke。默认测试不调用模型、
不需要 provider key、不访问网络；live smoke 必须显式开关并使用用户主动配置的 AuraBot
Pi Home。Frontend 只做回归，不能复活 Electron Task surface。

关键故障包括 spawn/startup/prompt、response correlation、非法 JSONL、stderr flood、
非零退出、超时、pause/resume/cancel、grace 后强杀、Backend shutdown、重启孤儿、
多 Task 并发、Pi Home 隔离和禁止全局 Pi discovery。

## 10. 实施阶段与历史边界

1. 写入已批准设计事实源。
2. 建立 Pi-native Extension Package 与精确版本依赖载体。
3. 实现模型、配置和 Python SQLite。
4. 实现严格 JSONL RPC 与唯一进程 owner。
5. 实现 Scheduler 与 TaskManager 垂直切片。
6. 接入 REST/SSE 与 FastAPI lifespan。
7. 真实 Pi offline smoke、全仓回归、文档状态切换和残留审计。

第一轮已经永久删除 AuraBot 自研 Harness、ReAct/Plan-and-Solve、Tool/Skill Group、
Capability、Adaptor、Agent SQL、MCP Gateway/server 以及 Electron TaskService、CLI
runner、Task repository/IPC/UI。Git 历史是唯一档案；第二轮不得以新名称恢复这些类图。

最终交付口径固定为：**Python Pi Task 后端垂直切片已实现；Electron、Main Agent、
Artifact 与发行打包尚未接入。**
