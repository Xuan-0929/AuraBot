# Main Agent v0 实现规格

## 目标

用最小、可测试的 Python 主对话运行时验证三件事：单主队列、主 Agent 独占长期记忆
读写、以及应用重启后的连续状态。v0 只处理桌面文字消息并返回确定性回复，不接真实
模型、MCP、Task Agent、TTS 或外部渠道。

模块职责仍以[主 Agent 设计](modules/main-agent.md)为准；本文件限定第一个可运行切片。

## 非目标

- 不迁移现有 TypeScript `ChatOrchestrator` 的全部 gate、Soul 或任务能力。
- 不让前端直接调用 `MemoryService`。
- 不实现多 profile 并发主会话、消息流式模型调用或完整 WebSocket 重连。
- 不修改现有 Electron SQLite 数据库的写入 owner。

## 组件

| 组件 | 责任 | v0 约束 |
| --- | --- | --- |
| `MainAgent` | 串行处理标准化事件、调用记忆、生成回复 | 一个进程只创建一个实例。 |
| `MainAgentStateStore` | 保存主 session、短期缓冲区、pending 状态和最近事件 | 独立于 Electron legacy SQLite。 |
| `ShortTermMemoryBuffer` | 聚合消息、记录主题断点和待总结片段 | 每个 profile 一份。 |
| `MemoryService` | 长期记忆 recall / remember | 只能由 `MainAgent` 调用。 |
| `DeterministicReplyGenerator` | 根据输入和 recalled memory 生成测试回复 | 仅 v0 / 测试使用，可替换。 |
| `MainAgentRouter` | 参数验证、请求确认与状态读取 | 不包含业务编排。 |

## 输入与处理顺序

v0 接受 `UserMessageEvent`：

```json
{
  "client_message_id": "uuid",
  "profile_id": "profile_default",
  "session_id": "main",
  "channel": "desktop",
  "content": "我喜欢晴天",
  "created_at": "2026-07-17T00:00:00Z"
}
```

每个事件严格执行以下顺序：

1. 校验 `profile_id`、`session_id`、`channel` 和非空内容。
2. 用 `(profile_id, client_message_id)` 去重；重复提交返回原结果，不再次写记忆。
3. 服务端创建 `event_id`，持久化用户消息和“处理中”状态，再放入单消费者队列。
4. 从短期缓冲区加载上下文，调用 `MemoryService.recall()`。
5. 将用户输入追加到短期缓冲区；若达到明确断点阈值，将最早的完整片段交由 Main Agent 决定是否 `remember()`。
6. 生成确定性回复，持久化 assistant 消息与处理结果。
7. 写入 `chat.message.completed` 内部事件，并返回可投递的事件 payload。

任一步失败时，保留已持久化的用户消息，将事件标记为 `failed`，且不得部分写入长期
记忆。记忆写入失败不丢失短期片段，片段保留为 pending 以便下一次维护事件重试。

## 持久化模型

`MainAgentStateStore` 需要四类记录：

| 记录 | 最少字段 | 目的 |
| --- | --- | --- |
| `main_sessions` | `profile_id`、`session_id`、`updated_at` | 保证每个 profile 的主 session 可恢复。 |
| `main_events` | `event_id`、`client_message_id`、`profile_id`、`kind`、`state`、`payload_json`、`created_at` | 去重、追踪和失败恢复。 |
| `short_term_segments` | `segment_id`、`profile_id`、`sequence`、`content`、`state` | 保存缓冲区和待总结片段。 |
| `pending_actions` | `action_id`、`profile_id`、`kind`、`payload_json`、`state` | 保存待写记忆与未来等待用户输入。 |

所有 JSON payload 使用 `schema_version: 1`。v0 不要求把这些表与现有 Electron
SQLite 合并，数据所有权见 [运行时数据所有权](runtime-data-ownership.md)。

## API 与事件

- `POST /main-agent/messages`：确认事件已经持久化并排队。
- `GET /main-agent/status`：返回运行状态、队列长度、当前 event id 和最近 session。
- `/ws`：v0 至少发出 `chat.message.started`、`chat.delta`、`chat.message.completed`、`chat.message.failed` 与 `agent.status.changed`。

字段、幂等和错误格式以[运行时契约](backend-runtime-contract.md)为准。路由层不得直接
调用 `MemoryService` 或访问任务管理器。

## 重启恢复

启动时：

1. 将状态为 `running` 的 `main_events` 标记为 `failed`，error code 为 `INTERRUPTED_BY_RESTART`。
2. 重新加载 `short_term_segments` 与 `pending_actions`。
3. 不自动重放用户消息，不自动重新写长期记忆，不自动发送用户可见通知。
4. 第一条后续用户消息可触发 pending 记忆维护，但必须使用原 event id 记录来源。

## 测试矩阵

| 场景 | 断言 |
| --- | --- |
| 首条消息 | 用户和 assistant 消息持久化；状态从 queued 到 completed。 |
| 同一 event id 重试 | 返回第一次结果；不重复写消息或长期记忆。 |
| recall | 回复生成器收到限定数量的 recalled memory。 |
| 断点片段 | 阈值触发后只写完整的最早片段，保留剩余上下文。 |
| remember 失败 | 用户消息保留，片段进入 pending，事件不伪装完成。 |
| 重启 | 旧 running 事件失败式恢复；短期片段与 pending 动作仍存在。 |
| 并发提交 | 队列按接受顺序串行处理；不会同时生成两个主回复。 |
| HTTP 校验 | 空内容、未知 channel 与重复 event id 返回契约规定的状态码和错误码。 |

## 完成条件

v0 只有在上述测试全部通过、`backend.main` 已挂载 Main Agent 路由、memory service
只由 Main Agent 访问，并且 Electron 仍未被迁移到这条写链路时才算完成。随后才能开始
Phase 2 的 WebSocket 与前端切换工作。
