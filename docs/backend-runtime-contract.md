# Backend Runtime Contract

## 当前契约

Python Backend 已在同一 Gateway/lifespan 内组合 Main Agent、Memory、realtime/speech 与
版本化 Task REST/SSE，健康路径保持：

~~~http
GET /health
~~~

成功响应必须精确为：

~~~json
{"ok":true,"service":"backend"}
~~~

当前公开 Task 路径为 `/api/v1/tasks`、`/api/v1/tasks/{taskId}` 和
`/api/v1/task-events`；完整方法、错误 envelope、乐观并发与 SSE cursor 以
[Pi Task Runtime](modules/task-agent.md) 为准。旧 `/task`、Plan、MCP、chat、debug
或 registry API 不恢复，未知路径使用 FastAPI 标准 404。

附加本地运维/Memory 路径：

- `GET /api/v1/readiness`：只读 lifecycle、SQLite 与 worker 状态，永不调用模型、
  embedding、Qdrant health、Pi subprocess 或 Task spawn。
- `GET /api/v1/metrics`：bounded 内存 counters/timing snapshot，不包含 transcript/content。
- `GET /api/v1/memories`、`POST /api/v1/memories/forget`、
  `POST /api/v1/memory-jobs/{jobId}/retry`：camelCase DTO；不暴露 durable payload/lease。

Task response 不暴露 `pendingQuestion`。POST 创建时 `timeoutSeconds` 可省略并由 YAML
默认值填充；显式 null 与范围外数字由 FastAPI 返回 422。Task 的已知 domain error
使用稳定 Task envelope；所有未识别 Backend 异常统一返回脱敏的
`500 internal_error`，message 固定为 `Backend request failed internally.`。

## 配置

Backend 在导入应用时通过 backend.config 读取 YAML：

- 默认路径：仓库根目录 config.yaml。
- 覆盖变量：AURABOT_CONFIG_PATH。
- 字符串支持必填的 ${VARIABLE} 与可选的 ${VARIABLE:-fallback}。
- 缺少配置或必需变量时启动失败，不回显 secret value。

health endpoint 在任何 owner 状态下都独立且不依赖模型或 Worker。新 `task` 段只配置 Pi-native Runtime，
不兼容已删除的 `task_runtime`、`task_model` 或 `local_mcp_server`。

## 生命周期

~~~mermaid
sequenceDiagram
  participant Launcher
  participant Uvicorn
  participant Config
  Launcher->>Uvicorn: backend.main:app
  Uvicorn->>Config: initialize YAML
  Uvicorn->>Config: construct Task + active profile owners
  Uvicorn->>Uvicorn: start Task → Memory → Main → realtime → speech
  Uvicorn-->>Launcher: GET /health = ok
  Launcher->>Uvicorn: SIGINT / SIGTERM
  Uvicorn->>Uvicorn: fence admission → speech/Main → Memory → Task
  Uvicorn-->>Launcher: normal process exit
~~~

Backend 不创建旧 BackendRuntime，也不启动 MCP。FastAPI lifespan 拥有 TaskManager、
一个 process-level event-driven MemoryWorker、active-profile MemoryBridge、MainAgentRuntime、
RealtimeHub 与 SpeechPipeline。配置完整时 Main runtime 使用惰性 OpenAI-compatible SSE
provider；缺少 chat key/endpoint/model 时为静默 unavailable。speech 默认 disabled；
import、startup、readiness、health/static route 均不做付费 probe。

Memory pending episode/episode/job/outbox 全部在 profile 既有 `memory.sqlite3`。确认用户输入
把 segments、last-confirmed timestamp 与 generation 原子持久化并重置五分钟 seal；重启按
剩余时间恢复，超期立即把 episode/job 与 pending delete 同事务提交。worker 用 SQLite
claim/lease/restart recovery 与 5/30/300 秒精确重试。
SQLite memory commit 与 Qdrant outbox 分离，所以 vector partial failure 不会重复 extraction。

Runtime 仅保留最近 128 个 invoke 诊断；ToolHarness 的 Observation terminal dedupe/outcome
各最多 256，ObservationLedger latest/dedupe 最多 256、events 最多 512。running/inflight
identity 不可驱逐，晚到的已驱逐 terminal settlement fail-closed；终态事实由 ContextStore 持久化。

## 边界规则

Task API 只能投影 Python Control Plane 的业务状态，不得暴露 Pi stdin/stdout、
内部 Tool Call、stderr、session 路径或旧 Task DTO。第一版只绑定 loopback，不承诺
远程认证或 CORS；远程开放前必须另行设计威胁模型。
