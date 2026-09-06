# AuraBot 系统模块

## 当前边界

~~~mermaid
flowchart TB
  CONSOLE["Console<br/>主对话 · 只读 Task"] -->|输入 IPC| MAIN["Electron Main"]
  SETTINGS["Settings<br/>保存 / 应用"] --> MAIN
  MAIN --> CONFIG["settings.json"]
  MAIN --> HISTORY["conversation-history.sqlite3"]
  MAIN --> PROFILE["profile.sqlite3<br/>身份 · 已应用人格投影"]
  MAIN -->|启动 / 重启 / 退出| BACKEND["唯一 Python Backend"]
  MAIN -->|转发文字| PET["Pet Renderer<br/>Live2D · 唯一 WebSocket · 音频"]
  PET -->|交付回执| MAIN
  PET <--> HUB["RealtimeHub"]
  BACKEND --> HUB
  HUB <--> AGENT["MainAgentRuntime"]
  AGENT --> CONTEXT["ContextStore"]
  AGENT --> MEMORY["MemoryService"]
  AGENT --> OPS["TaskOperations<br/>三秒提案 · 已接管控制"]
  OPS --> TASK["TaskManager"]
  TASK -->|最新事实| CONTEXT
  TASK --> PI["Pi RPC processes"]
  TASK -->|只读 task.snapshot SSE| MAIN
~~~

## 模块职责

| 模块 | 唯一职责与边界 |
| --- | --- |
| Electron Main | 配置保存/应用、单 Backend 子进程监督、窗口、Profile 身份、可见历史和只读 Task 投影。 |
| Pet | 持有唯一文字/语音 WebSocket；负责麦克风、播放、完整交付回执。 |
| Console | 经 IPC 提交文字、分页显示历史、只读显示 Task 标题/状态。 |
| Main Agent | 拥有对话 Context、模型调用、Memory 编排和 Task 工具；内部推理不作为回复展示。 |
| Memory | 召回、提炼、长期 SQLite/Qdrant 存储；不提供外部管理 API/UI。 |
| TaskOperations | Main Agent 内的提案/控制 owner；单提案 worker、三秒冻结恢复，提交后结清，无无限投影重试。 |
| TaskManager | Python 控制面，拥有状态、队列、事件和 Pi 生命周期；业务入口只有 Main Agent，直接向桌面只输出状态 SSE。 |
| Pi | 执行面，每个活动 Task 一个 RPC 进程，负责自身 Agent Loop、模型与工具。 |

文字在没有语音资源时也能运行。Profile token 激活 Context、Memory 和文字 Runtime；`voice.start` 才加载语音依赖与资产。

## 数据规则

1. `settings.json` 是运行设置和人格的事实源；Python 仅读取启动快照，不热加载。
2. `profile.sqlite3` 保存桌面身份及已应用人格投影，不成为第二套设置入口。
3. `conversation-history.sqlite3` 只保存接受/交付回执，按游标分页；它不参与模型 Context 或 Memory 召回。
4. Profile 内的 Context、Memory SQLite/Qdrant 与独立 Task SQLite 由 Backend 拥有。
5. 不恢复旧 Electron Chat/Memory/Task 业务服务、兼容 DTO 或 CLI runner。

路径与重启协议见 [配置与生命周期](configuration-and-backend-lifecycle.md)，接口见 [后端契约](backend-runtime-contract.md)，Pi 细节见 [Task Runtime](modules/task-agent.md)。

## 2026-09-06 架构收敛

- Electron 的 ProfileStorage 只建当前身份/人格两表；旧 Chat/Gate/Memory/主动触发、旧配置与历史迁移已退役。
- Context 的 `context_task_facts` 按稳定 ID/version 保留最新事实；完整 Task 历史仍只在 TaskStore。
- ProfileResources 唯一持有 Speech。文字打断重置旧采集回合并取消旧 TTS，复用 worker/provider/detector。
- 详细改动与验证见 [架构优化记录](architecture-optimization.md)。
