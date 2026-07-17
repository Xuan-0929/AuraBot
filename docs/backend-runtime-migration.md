# 后端运行时迁移路线

## 目标

将 AuraBot 从“Electron main process 承载核心业务、Python 仅做只读网关”的 MVP，逐步迁移为“Electron 负责界面和本地壳层、Python 后端主进程承载主对话、记忆、任务和实时事件”的单后端运行时。

迁移不采用大爆炸重写。每一阶段都必须保留可运行路径、明确数据所有权，并能单独验证。

## 不做什么

- 不在迁移早期拆分多个常驻微服务。
- 不同时维护两套可写的聊天、记忆或任务真相源。
- 不先接入 QQ、微信、飞书、完整 MCP 或本地重型 TTS。
- 不承诺任务 true resume、复杂多 Agent 协作或离线提醒，直到基础事件与持久化边界稳定。

## 阶段

### Phase 0：运行时基线

统一配置模型、存储路径、端口、日志和 Electron 对后端的启动/停止/健康检查。明确 YAML 是 Python runtime 配置，Electron JSON 是旧 TypeScript runtime 配置；迁移期间不能互相隐式读取。

验收：普通开发启动无需手动猜测 Python 解释器；配置缺失、端口冲突和后端退出都有用户可理解的错误。

### Phase 1：Main Agent + Memory 最小闭环

实现一个后端单消费者 Main Agent：接收桌面文字消息，持久化主 session，维护短期缓冲区，调用 Python MemoryService 进行 recall / remember，并返回确定性测试回复。此阶段不接真实模型、不分配真实任务。

验收：可用 API 测试消息写入、召回、断点片段写入、重启后的会话和 pending 状态恢复。

### Phase 2：HTTP 写接口与事件流

增加消息提交、主状态查询和 WebSocket 事件通道。先覆盖 `chat.message.started`、`chat.delta`、`chat.message.completed`、`agent.status.changed`，Electron 改为通过后端完成一条消息的垂直链路。

验收：前端不再为这条后端消息链路直接调用 TypeScript ChatOrchestrator；断线与后端错误可显示且不会丢失已提交消息。

### Phase 3：Task Manager 运行时

将任务定义、状态转换、进度事件、等待用户信息和 artifact registry 持久化到后端。先支持 deterministic harness，再逐步接入现有 CLI runner。任务事件只能通过 Main Agent 转为用户话术。

验收：创建、查询、打断、失败、重试和 artifact 校验都有后端测试；运行中任务重启后被明确标记为失败式恢复，不伪造 resume。

### Phase 4：迁移成熟业务能力

按依赖将 gate、模型健康、主动提醒、scheduler 和现有任务控制从 TypeScript core 迁移到 Python。每迁移一条写链路，就删除对应的双写或 fallback 业务分支。

验收：Python 成为消息、记忆和任务的唯一写入 owner；Electron 保持界面、IPC 和本地窗口能力。

### Phase 5：可选能力

在主链路稳定后再接 connector-hub、MCP、稳定 STT、可选 TTS worker、Windows 实机回归、离线提醒和复杂任务 resume。

## 数据所有权

| 数据 | Phase 0-1 | 目标 |
| --- | --- | --- |
| 主消息与任务 | 现有 TypeScript SQLite | Python runtime 唯一写入，Electron 通过 API 访问。 |
| 短期记忆与 pending 状态 | 新建 Python runtime 持久化 | Python Main Agent。 |
| 长期记忆 | Python MemoryService 实验存储 | Python Main Agent 是唯一读写入口。 |
| 窗口位置与 UI 偏好 | Electron | Electron，除非需要跨渠道同步。 |
| API 密钥 | 分离配置，禁止自动复制 | 迁移后由一个显式配置模型统一管理。 |

## 文档与验收

任何阶段开始前更新 [运行时契约](backend-runtime-contract.md)，完成后更新 [当前状态](aurabot-development-status.md)。模块边界变化同时更新 `modules-v2.md`、`modules/main-agent.md` 或 `modules/task-agent.md`。
