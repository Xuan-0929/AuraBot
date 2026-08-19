# AuraBot 产品需求

## 产品定义

AuraBot 是带 Live2D 桌宠界面的陪伴型桌面 AI。用户通过文字、语音或外部渠道与
唯一 Main Agent 交互；Memory 提供连续关系，未来 Task Runtime 处理受控的外部工作。

## 当前产品状态

- Electron 提供桌宠、控制台、普通聊天、Memory、设置和非 Task 主动提醒。
- Python Backend 当前仅提供健康检查。
- Task 处于架构迁移空窗期，输入会得到固定不可用回复。
- 旧自研 Harness/MCP/CLI runner 已删除；Pi Task Runtime 尚未实现。

## 目标能力

### 交互与陪伴

- Live2D 常驻桌面但不过度遮挡工作。
- 主控制台显示唯一主对话历史。
- 支持文字、未来 STT/TTS，以及 QQ、微信、Telegram 等 ingress。
- Gate 对外部渠道碎片消息进行合并与回复时机判断。
- Main Agent 串行处理对话，并独占长期 Memory 的业务读写。

### Task

Main Agent 未来可创建、查询和中断 Task。Task 执行必须采用
[Pi Task Runtime](modules/task-agent.md) 定义的 Control Plane / Execution Plane
边界：AuraBot 管理产品状态和进程，Pi 管理 Agent Loop、模型、工具和上下文。

### 主动能力

Memory follow-up 和 idle nudge 可主动触达用户。定时 Task、Task 完成提醒和 Task
问答在新 Runtime 实现前均不可用。

## 目标进程模型

~~~mermaid
flowchart TB
  INGRESS["Ingress / Media<br/>channels · VAD · STT · Gate"] --> CORE
  subgraph CORE["Aura Core"]
    MAIN["Main Agent"] --> MEMORY["Memory"]
    MAIN -. "future" .-> TM["Python TaskManager"]
  end
  TM -. "one task / one RPC process" .-> PI["Pi Execution Plane"]
  UI["Electron / Vue"] <--> CORE
~~~

本轮不要求物理拆分 Ingress，但不同模块不得同时成为同一业务数据的写入 owner。

## 非功能性需求

- 配置和错误不得泄露密钥。
- 每类业务数据只有一个 owner。
- Task Worker 故障不得拖垮 Main Agent 或 Memory。
- Task 无 AuraBot 级自动重跑；重跑创建新执行尝试。
- macOS 为主要开发平台，Windows 10/11 x64 为保留打包目标。
- 前端通过明确的 HTTP/事件契约使用 Backend，不复制 Python 业务核心。

## 非目标

- 第一轮不实现 Pi、Node Worker、新 Task API/UI 或兼容层。
- 不保留 ToolGroup、SkillGroup、Capability lease、Harness Adapter 或 AuraBot MCP
  server。
- 不承诺 true resume、第三方 Package 管理或 Task approval UI；这些属于第二轮后续。
