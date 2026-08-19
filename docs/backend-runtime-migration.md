# Backend Runtime Migration

## 当前阶段

第一轮已经选择破坏性断点：先删除旧执行面，再从清晰边界实现 Pi-native Runtime。

~~~mermaid
flowchart LR
  OLD["旧 Python Harness/MCP<br/>旧 TS Task runners"] -->|"第一轮删除"| NOW["health-only Backend<br/>Task unavailable"]
  NOW -->|"第二轮设计与实现"| CONTROL["Python TaskManager<br/>Control Plane"]
  CONTROL --> PI["Pi RPC<br/>Execution Plane"]
~~~

## 第一轮完成条件

- backend/agent、Task/Plan gateway/application/runtime 和 mcp_server 不存在。
- Electron TaskService、repositories、runners、IPC、UI 与 reminder 不存在。
- 旧 Task 数据按 migration 清理。
- Backend 只有 health contract。
- 普通 chat、Memory、Live2D、窗口和非 Task proactive 能力通过回归。

## 第二轮顺序

1. 定义 Task 产品模型、持久化 owner、状态机和业务事件。
2. 固定项目级 Pi/Node 版本、CLI 定位与 PI_CODING_AGENT_DIR 生命周期。
3. 定义最小 JSONL RPC adapter 与一 Task 一进程 supervisor。
4. 覆盖正常完成、非零退出、崩溃、超时、中断、重启和清理。
5. 接入 Main Agent，并在业务契约稳定后新增 API/event projection。
6. 最后实现新 Task UI、Artifact 展示与发行打包。

第二轮不得复制旧类图或恢复 TypeScript CLI runner。完整目标见
[Pi Task Runtime](modules/task-agent.md)。
