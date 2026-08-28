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
- `GET /api/v1/readiness`：只读本地 owner 状态，不 probe 外部服务。
- `GET /api/v1/metrics`：bounded counters/timing，不包含对话内容。

Main Agent 不提供 Profile 专用 Memory browse/forget/retry API，也不会为 HTTP 管理请求打开 inactive
Profile 的 DB/Qdrant。已知 domain error 使用稳定 envelope；未知异常统一返回脱敏
`500 internal_error`。

## Process 与 Profile ownership

~~~mermaid
flowchart TB
  PROCESS["FastAPI lifespan"] --> TASK["共享 TaskManager"]
  PROCESS --> HUB["RealtimeHub"]
  PROCESS --> ACTIVE["ActiveProfileManager"]
  ACTIVE -->|0 或 1| PROFILE["ProfileResources"]
  PROFILE --> RT["MainAgentRuntime"]
  PROFILE --> SPEECH["SpeechService"]
  PROFILE --> MEMORY["MemoryService"]
  PROFILE --> CONTEXT["ContextStore"]
  PROFILE --> CLIENTS["Chat/Tavily clients"]
~~~

Process startup 不创建 ProfileResources。合法 token 请求才激活；Profile switch 串行关闭旧资源后
打开新资源。inactive Profile 没有 timer、DB connection 或 Qdrant client。

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

Main Agent 的 `ContextStore` 是唯一未归档 conversation buffer。stable/final user segment 与 Renderer
完整 ACK 的 assistant clause 形成稳定批次；partial transcript、内部 Observation/Task/think 事件不提交。

~~~mermaid
flowchart LR
  INPUT["confirmed input"] --> BUFFER["context.sqlite3<br/>conversation buffer"]
  BUFFER -->|idle 300s| INGEST["MemoryService.remember_conversation"]
  INGEST -->|async LLM| STORE["memory.sqlite3 + Qdrant"]
  STORE -->|成功，含零 memories| CLEAR["按 through_sequence 清理"]
  INGEST -->|失败或取消| KEEP["保留 buffer"]
  KEEP -->|仍空闲 300s| INGEST
  INPUT -.->|text / VAD / STT 取消| INGEST
~~~

长耗时提炼使用可取消 async HTTP。取消发生在 LLM 阶段时不创建 episode 或长期记忆；LLM 成功后复用
既有 MemoryService 的本地 SQLite/Qdrant 持久化链路。同一 Profile 和稳定 batch `source_id` 的重试
幂等。Memory 成功（包括提炼结果为空）后 Runtime 才清理本批 Context；失败、取消、Profile switch
或 shutdown 都保留 buffer。最后用户活动时间持久化在 `context.sqlite3`，重新激活后恢复剩余 idle 时间。

每次 control snapshot 直接调用 `MemoryService.recall`。召回策略、提炼规则与长期存储均归 Memory
模块所有；Main Agent 不缓存、筛选或重新实现 lexical/vector 合并。

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
