# Main Agent v0 实现规格

> 状态：历史目标规格仍可作为未来 Main Agent 切片参考；当前 Backend 已有独立 Task
> REST/SSE，但本 Main Agent 规格尚未接入生产路由。

## 目标

验证单主队列、唯一主 session、Main Agent 独占长期 Memory 业务读写，以及应用重启
后的连续状态。v0 只处理桌面文字消息，不实现 Task、Pi、TTS 或外部渠道。

## 组件边界

| 组件 | 责任 |
| --- | --- |
| MainAgent | 串行处理标准化事件、调用 Memory、生成回复。 |
| MainAgentStateStore | 保存主 session、事件和 pending 状态。 |
| ShortTermMemoryBuffer | 聚合消息、断点与待总结片段。 |
| MemoryService | recall/remember，只由 Main Agent 调用。 |
| MainAgentRouter | 参数验证与传输，不做业务编排。 |

## 流程

~~~mermaid
sequenceDiagram
  participant UI
  participant Main as Main Agent
  participant Memory
  participant Store
  UI->>Main: normalized user message
  Main->>Store: persist + deduplicate
  Main->>Memory: recall
  Main->>Store: append short-term context
  Main-->>UI: assistant reply
  Main->>Store: completed
~~~

## Task 边界

v0 不迁移 Task。未来 Main Agent 只能调用 Python TaskManager 的产品接口；Pi 架构
见 [Pi Task Runtime](modules/task-agent.md)。不得把 Harness、ToolGroup、MCP 或
Worker RPC 暴露进 Main Agent。

## 实施前提

只有新 Main Agent 契约、持久化 owner 和路由被重新批准后才能实现。当前不可把本
规格中的建议路径误写为已存在接口；现行 Backend 契约见
[Backend Runtime Contract](backend-runtime-contract.md)。
