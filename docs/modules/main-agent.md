# Main Agent

## 职责

Main Agent 是 AuraBot 唯一长期对话主体。它串行处理标准化用户事件，读取和维护
Memory，生成用户可见回复，并在未来通过 Python TaskManager 使用 Task 能力。

当前 Electron MVP 仍承载普通聊天编排；Python Main Agent 尚未接入生产请求路径。
当前 Task 不可用，Main Agent 不得绕过空窗期直接启动任何 CLI。

## 目标关系

~~~mermaid
flowchart LR
  GATE["Gate / normalized input"] --> MAIN["Main Agent"]
  MAIN --> MEMORY["Memory"]
  MAIN -->|"create · query · interrupt"| TM["TaskManager Control Plane"]
  TM -->|"JSONL RPC"| PI["Pi Execution Plane"]
  TM --> EVENTS["Task business events"]
  EVENTS --> MAIN
  MAIN --> USER["User-visible reply"]
~~~

Main Agent 只依赖 TaskManager 的产品接口。Harness、Capability lease、ToolGroup、
SkillGroup、Adaptor、MCP gateway 和 Pi 内部 event 都不得成为 Main Agent API。

## 核心流程

1. Gate 将输入归一化为 chat 或 task 意图。
2. chat 输入进入主对话、Memory recall 和回复流程。
3. 当前 task 输入只返回“Task 功能正在迁移，当前暂不可用。”。
4. 未来 task 输入由 Main Agent 向 TaskManager 创建业务 Task。
5. Main Agent 查询/中断 Task 时只使用稳定业务标识，不持有 Worker 对象。
6. TaskManager 将完成、失败、询问等产品事件投影回 Main Agent。

## 并发与会话

- Main Agent 自身单队列、单消费者，保持一个用户感知的连续主 session。
- Task Worker 可并发，但不得并发改写 Main Agent 上下文。
- Task 询问未来通过业务事件排队回到 Main Agent，不使用旧 waiting Harness resume。
- 应用重启后不伪装恢复已丢失的 Pi 进程。

## Memory 边界

- Main Agent 是长期 Memory 的业务读写入口。
- Task 结果可由 Main Agent 决定是否写入 Memory。
- Pi Worker 不直接成为 Memory owner。
- Memory follow-up 与 idle nudge 可独立于 Task 继续运行。

## Task 安全与资源边界

Task 的进程隔离、超时、中断、Artifact 与 Pi Package 规则以
[Pi Task Runtime](task-agent.md) 为唯一事实源。Main Agent 不建立另一套 allowlist、
sandbox、Tool schema 或 capability manager。

## 当前验收

- 普通聊天和 Memory 不依赖已删除 Task 模块。
- task 意图生成固定普通 assistant 回复，不创建 session、Task、event 或进程。
- 公共 chat result 与 application initial state 不暴露旧 Task DTO。
- 文档和 UI 不宣称新 Task Runtime 已可用。
