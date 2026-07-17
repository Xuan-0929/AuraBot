# 后端运行时契约 v1

本契约定义 Python runtime 迁移的第一批稳定边界。它描述目标接口；未实现的字段不能被前端假定为可用。接口实现前必须在[Main Agent v0 实现规格](main-agent-v0-implementation.md)和[运行时数据所有权](runtime-data-ownership.md)中确认 owner 与验收条件。

## 配置

| 范围 | 当前文件 | 约束 |
| --- | --- | --- |
| Python 后端 | 根目录 `config.yaml`，可由 `AURABOT_CONFIG_PATH` 覆盖 | 由 `backend.config` 在启动前加载。 |
| Electron 旧 runtime | Electron userData 下的 `aurabot.config.json` / `config.json` | 仅服务当前 TypeScript core。 |

迁移期间两套配置不自动同步。新增 Python 功能必须只读取 Python 配置；新增 Electron 旧功能必须只读取 Electron 配置。合并配置属于 Phase 0 的显式迁移任务，启动和失败策略见[配置与后端生命周期](configuration-and-backend-lifecycle.md)。

目标 Python runtime 配置的新增键必须放在明确的命名空间内：

```yaml
runtime:
  schema_version: 1
  storage_root: data/runtime
main_agent:
  enabled: false
  event_queue_limit: 128
memory:
  storage_root: data/memory
```

这些键在实现前只是契约，不代表当前 `config.example.yaml` 已支持。未知键必须被记录为配置错误或显式忽略，不能静默改变运行行为。

## HTTP

### `POST /main-agent/messages`

提交一个已经由桌面端明确发送的文字消息。

```json
{
  "client_message_id": "uuid",
  "profile_id": "profile_default",
  "session_id": "main",
  "channel": "desktop",
  "content": "你好",
  "schema_version": 1
}
```

`client_message_id` 在同一 profile 内唯一，重复提交必须返回第一次接受结果，且不得再次
写入消息或记忆。`content` 去除首尾空白后不能为空，最大长度由服务端配置控制。
`session_id` 在 v1 只能为 `main`，`channel` 在 v1 只能为 `desktop`。

返回值确认消息已持久化并进入主队列；生成结果通过 WebSocket 发送。

```json
{
  "message_id": "uuid",
  "event_id": "uuid",
  "accepted": true,
  "state": "queued",
  "schema_version": 1
}
```

### `GET /main-agent/status`

返回主 Agent 是否运行、队列长度、当前处理事件和最近的主 session 标识。不得返回模型提示、API 密钥或完整内部错误。

```json
{
  "state": "idle",
  "queue_depth": 0,
  "current_event_id": null,
  "session_id": "main",
  "schema_version": 1
}
```

`state` 的允许值为 `idle`、`processing` 和 `unavailable`。

## WebSocket

端点：`/ws`。

每条事件都使用以下包络：

```json
{
  "event_id": "uuid",
  "type": "chat.delta",
  "schema_version": 1,
  "timestamp": "2026-07-17T00:00:00Z",
  "profile_id": "profile_default",
  "session_id": "main",
  "message_id": "uuid",
  "sequence": 2,
  "payload": {}
}
```

`sequence` 在同一 `message_id` 内单调递增。客户端发现缺号、不同 `schema_version` 或
未知事件时，必须重新请求状态或历史，不得自行猜测缺失内容。

v1 允许的事件：

- `chat.message.started`
- `chat.delta`
- `chat.message.completed`
- `chat.message.failed`
- `agent.status.changed`
- `task.updated`
- `notification.push`

`chat.message.started` 和 `chat.message.completed` 的 `payload` 至少包含角色与最终状态；
`chat.delta` 的 `payload` 为 `{ "delta": "..." }`；`chat.message.failed` 的 `payload`
只包含公开错误对象。每个 accepted message 最终只能有一个 completed 或 failed 事件。

任务线程、memory service 和 scheduler 不能直接对 WebSocket 连接写入；它们只向 Main Agent / 事件总线提交内部事件。

## 数据与权限

1. Main Agent 是长期记忆唯一读写者；memory service 只提供能力，不主动决定写入。
2. Task Agent 不直接面向 HTTP、WebSocket 或长期记忆。
3. 输出 artifact 必须登记，打开前必须验证其属于对应任务。
4. 高风险、不可逆或越过 sandbox 的操作必须转为 `WAITING_FOR_INFO`。

## 错误

所有 API 错误使用：

```json
{
  "error": {
    "code": "CONFIGURATION_ERROR",
    "message": "用户可理解的说明",
    "recoverable": true
  }
}
```

v1 错误码：

| Code | HTTP | 含义 |
| --- | --- | --- |
| `VALIDATION_ERROR` | 422 | 请求字段、channel 或 schema version 不合法。 |
| `CONFIGURATION_ERROR` | 503 | 后端配置或依赖不满足启动条件。 |
| `MAIN_AGENT_UNAVAILABLE` | 503 | Main Agent 未启用、未初始化或正在关闭。 |
| `IDEMPOTENCY_CONFLICT` | 409 | 同一 client message id 使用了不同 payload。 |
| `SCHEMA_VERSION_MISMATCH` | 409 | 客户端或存储版本不兼容。 |
| `INTERNAL_ERROR` | 500 | 未分类的内部失败；message 必须可理解且不泄露细节。 |

内部 traceback、runner 命令、任务 id、模型提示和密钥不得直接展示给用户。
