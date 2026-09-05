# AuraBot 产品需求

更新：2026-09-05。本轮范围以 [前端集成记录](frontend-integration-work.md) 中的已批准需求为准。

## 产品定义

AuraBot 是带 Live2D 桌宠的陪伴型桌面 AI。用户通过文字或语音与唯一 Main Agent 交互，Memory 提供连续上下文，TaskManager 与 Pi 执行用户委托的工作。

## 用户体验

- 桌宠保留 Live2D、拖动、缩放和鼠标穿透。控制台承载主对话与 Task 状态；设置承载模型、语音、人格、桌宠和工作目录。
- 默认文字模式，首次运行不要求语音资产。未配置某项能力时，仍可打开设置；使用该能力时给出明确错误。
- Pet 持有唯一文字/语音连接。控制台文字经 Electron IPC 转发给 Pet；开启麦克风后才采集音频，停止语音后可继续文字交流。
- 可见历史只包含已接收的用户消息和 Main Agent `say_to_user` 的已交付内容。推理、工具结果和未确认 STT partial 不出现在历史中。
- 语音按句交付：整句实际输出完成 → Electron 持久化并展示 → Pet ACK。取消、替换或中断的未完成句不入历史。历史独立存储、分页读取，不替代或回灌 Backend Context。

## 设置与应用

Electron 拥有一个 JSON 配置。模型、语音、人格与工作目录均使用“编辑 → 保存 → 应用”流程。

~~~mermaid
flowchart LR
  EDIT["编辑草稿"] --> SAVE["保存 JSON<br/>当前运行不变"]
  SAVE --> APPLY["应用已保存版本"]
  APPLY --> BUSY{"Backend / 人格变化<br/>且存在忙碌工作？"}
  BUSY -->|是| CONFIRM["显示 Task / Memory / 对话影响"]
  CONFIRM -->|取消| SAVE
  CONFIRM -->|仍然应用| RESTART["阻止新工作 → 退出旧 Backend → 启动新 Backend"]
  BUSY -->|否，有 Backend 变化| RESTART
  RESTART --> READY["健康与版本确认后标记已应用"]
  APPLY -->|只有桌面设置变化| DESKTOP["应用桌面设置"]
~~~

后台忙碌判断包含 Task、Memory 实际写入、Main Agent 调用与未完成交付；空闲 Memory 定时器不算正在写入。新进程失败时明确保留待应用/错误状态，不自动重启或启动第二份 Backend。

## Task 与 Memory

Task UI 只展示标题和状态。创建、查询、问题回答、暂停、恢复、取消、终态删除和重跑由 Main Agent 工具完成；重跑创建新 Task，不承诺外部副作用 exactly-once。TaskManager 是唯一状态 owner，Pi 是执行进程，详见 [Task Runtime](modules/task-agent.md)。

Memory 由 Backend 内部维护。主对话在空闲边界归档，已封存批次由独立 worker 提炼；新输入不取消已封存写入。移除外部 Memory 管理 API 和设置页，不恢复旧 Electron Memory 服务。

## 进程与数据边界

~~~mermaid
flowchart TB
  E["Electron Main<br/>settings.json · 进程监督 · 交付历史"] --> B["唯一 Python Backend"]
  C["Console"] -->|IPC| E
  E -->|输入| P["Pet<br/>唯一 WebSocket · 麦克风 · 播放"]
  P <--> M["Main Agent"]
  B --> M
  M --> CTX["Context"]
  M --> MEM["Memory"]
  M --> TM["TaskManager"]
  TM --> PI["每个活动 Task 一个 Pi RPC 进程"]
~~~

配置和错误不得泄露密钥；窗口不复制 Backend 业务逻辑。本轮重建 AuraBot 运行数据，不迁移旧配置/数据库，保留模型资产、缓存与共享第三方数据。

## 非目标与验收边界

本轮不恢复旧 Harness/MCP/CLI runner、Memory 管理页、Task 手动控制按钮或独立文本会话，不新增外部渠道、附件执行、Artifact 管理或生产运行时打包。macOS 为主要开发平台；Windows 发行闭环仍需单独验收。

真实麦克风、输出设备和 Provider 验收需独立完成；单元测试、模拟回执和本地 readiness 均不能替代。实际完成情况以集成记录为准。
