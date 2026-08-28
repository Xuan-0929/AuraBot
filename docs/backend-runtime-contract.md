# Backend Runtime Contract

## HTTP 边界

健康响应固定为：

~~~http
GET /health

{"ok":true,"service":"backend"}
~~~

公开 Task 路径为 `/api/v1/tasks`、`/api/v1/tasks/{taskId}`、`/api/v1/task-events`；完整契约见
[Pi Task Runtime](modules/task-agent.md)。旧 `/task`、Plan、MCP、chat、debug 与 registry API 不恢复。

Main Agent 附加路径：

- `POST /api/v1/main-agent/realtime-token`：严格 Origin；JSON body 为 `profileId` + `soul`。
- `WS /api/v1/main-agent/realtime`：一次性 capability + `aurabot.main-agent.v1`。
- `GET /api/v1/profiles/{profileId}/memories`。
- `POST /api/v1/profiles/{profileId}/memories/forget`。
- `POST /api/v1/profiles/{profileId}/memory-jobs/{jobId}/retry`。
- `GET /api/v1/readiness`：只读本地 owner 状态，不 probe 外部服务。
- `GET /api/v1/metrics`：bounded counters/timing，不包含对话内容。

Memory API 只允许 active Profile；不匹配返回 `409 profile_not_active`，不会临时打开 inactive DB。
已知 domain error 使用稳定 envelope；未知异常统一返回脱敏 `500 internal_error`。

## Process 与 Profile ownership

~~~mermaid
flowchart TB
  PROCESS["FastAPI lifespan"] --> TASK["共享 TaskManager"]
  PROCESS --> HUB["RealtimeHub"]
  PROCESS --> ACTIVE["ActiveProfileManager"]
  ACTIVE -->|0 或 1| PROFILE["ProfileResources"]
  PROFILE --> RT["MainAgentRuntime"]
  PROFILE --> SPEECH["SpeechService"]
  PROFILE --> MEMORY["ProfileMemory"]
  PROFILE --> CONTEXT["ContextStore"]
  PROFILE --> CLIENTS["Chat/Tavily clients"]
~~~

Process startup 不创建 ProfileResources。合法 token 请求才激活；Profile switch 串行关闭旧资源后
打开新资源。inactive Profile 没有 timer、worker、DB connection 或 Qdrant client。

Profile 数据：

~~~text
profiles/<profile_id>/context.sqlite3
profiles/<profile_id>/memory.sqlite3
profiles/<profile_id>/qdrant/
~~~

没有 Backend Profile/Soul 文件。旧数据不迁移。

## Realtime contract

Client binary 永远是 640-byte mic PCM16LE 16 kHz；Server binary 永远是当前唯一 clause 的 TTS
PCM。JSON 仅包含 session ready/error、playback pause/resume、clause offer/audio_end/cancel 和
renderer ACK。TCP/WebSocket 提供顺序，因此应用层没有 AUR1 header、seq、command ledger、epoch、
stream ID 或 sample coordinate。

Mic queue 最多 50 frames，满时丢最旧。Speech delivery 串行且等待物理 ACK；只有 PLAYING clause
的 complete ACK 能提交 assistant Context。

## Memory durability

`memory.sqlite3` 只有 pending episode、统一 jobs、items 和 FTS。active Profile 的单 worker 处理
`extract` / `vector_delete`：

~~~mermaid
stateDiagram-v2
  [*] --> pending
  pending --> running
  running --> indexing: extraction result persisted
  indexing --> completed: SQLite + Qdrant upsert
  running --> pending: retry 5/30/300s
  indexing --> pending: retry 5/30/300s
  running --> failed: retries exhausted
  indexing --> failed: retries exhausted
  failed --> pending: manual retry
~~~

启动把残留 running/indexing 复位为 pending。Memory ID 确定性生成，整 job 可重放。forget 先删除
SQLite truth；Qdrant delete 失败写同一 jobs 表的 vector_delete，不创建 outbox。

Recall 同时启动 lexical/vector；lexical 必须完成，vector 最多等待 300 ms。最终 fresh-read SQLite，
所以 forget 对同一 query 立即生效。

## Shutdown

~~~mermaid
sequenceDiagram
  participant L as Lifespan
  participant H as RealtimeHub
  participant P as ActiveProfile
  participant T as TaskManager
  L->>H: stop accepting + close voice
  L->>P: Runtime → Speech → Memory → stores
  L->>T: shutdown shared control plane
~~~

取消 lifespan waiter 不改变 ownership 顺序。queued Task 保留；running Task 的重启语义仍由
TaskManager 定义。
