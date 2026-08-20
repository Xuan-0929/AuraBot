# Backend Runtime Migration

## 当前阶段

第一轮已经选择破坏性断点：先删除旧执行面，再从清晰边界实现 Pi-native Runtime。

~~~mermaid
flowchart LR
  OLD["旧 Python Harness/MCP<br/>旧 TS Task runners"] -->|"第一轮删除"| GAP["health-only Backend<br/>Task unavailable"]
  GAP -->|"第二轮已实现"| CONTROL["Python TaskManager<br/>Control Plane"]
  CONTROL --> PI["Pi RPC<br/>Execution Plane"]
~~~

## 第一轮完成条件

- backend/agent、Task/Plan gateway/application/runtime 和 mcp_server 不存在。
- Electron TaskService、repositories、runners、IPC、UI 与 reminder 不存在。
- 旧 Task 数据按 migration 清理。
- Backend 只有 health contract。
- 普通 chat、Memory、Live2D、窗口和非 Task proactive 能力通过回归。

## 第二轮已完成切片

1. 产品状态、Python-owned SQLite 与业务事件已经落地。
2. Pi 0.84.2 私有依赖载体和 Pi-native Extension Package 已经落地。
3. 严格 JSONL RPC、一 Task 一进程 supervisor、FIFO 与并发准入已经落地。
4. TaskManager 已收口正常完成、错误、暂停、session 恢复、取消、重启和 shutdown。
5. 现有 Gateway 已加入版本化 REST 与 SSE 后端垂直切片。
6. Main Agent、Electron、Artifact 与生产发行打包仍留在后续切片。

第二轮不得复制旧类图或恢复 TypeScript CLI runner。完整目标见
[Pi Task Runtime](modules/task-agent.md)。
