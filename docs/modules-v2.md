# AuraBot 系统模块

## 当前实现边界

~~~mermaid
flowchart LR
  R["Electron Renderer<br/>Vue · Live2D · Console"] <--> M["Electron Main<br/>Chat · Memory · Window · Notification"]
  M --> DB["SQLite<br/>profiles · sessions · messages · memory · events"]
  M --> B["Python Backend<br/>GET /health only"]
  B --> PM["Python Memory modules<br/>保留，未由 Task 使用"]
~~~

旧 TypeScript Task core、Python Task execution framework 与独立 MCP server 已删除。
当前没有 Task 数据 owner，也没有 Task API/UI。

## 目标边界

~~~mermaid
flowchart TB
  UI["Electron / Vue<br/>presentation only"] <--> API["Aura Core API / events"]
  subgraph CORE["Aura Core Process"]
    MAIN["Main Agent"]
    MEMORY["Memory"]
    TM["TaskManager Control Plane"]
    MAIN --> MEMORY
    MAIN --> TM
  end
  API --> MAIN
  TM -->|"JSONL RPC"| PI["Pi process per Task"]
~~~

### Main Agent

拥有唯一长期对话上下文，串行处理用户事件，协调 Memory，并通过未来 TaskManager
控制 Task。它不执行 Pi Tool Call。

### Memory

保存短期与长期记忆、关联和 follow-up。Task Runtime 不得绕过 Main Agent 任意改写
用户长期记忆。

### TaskManager

未来属于 Python Control Plane：产品状态、队列、并发、进程生命周期、业务事件、
结果和 Artifact。具体约束只见 [Pi Task Runtime](modules/task-agent.md)。

### Pi Worker

未来每个运行中 Task 对应一个 Pi RPC 进程。Pi 是 Execution Plane，不是 AuraBot
常驻微服务，也不由独立 AuraBot MCP server 包装。

### Electron

保留窗口、渲染、Live2D、用户交互和网络桥。新 Task UI 只能消费未来 Python
投影，不得恢复 TypeScript TaskService 或 CLI runner。

## 数据与依赖规则

1. 一个业务对象只能有一个写入 owner。
2. 不通过兼容 DTO 保留旧 Task schema。
3. Pi Tool/Skill/Package 必须使用 Pi 原生协议。
4. 新 Task 代码在第二轮需求与接口确定前不得预建。
