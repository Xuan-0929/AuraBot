# AuraBot

AuraBot 是带 Live2D 桌宠的陪伴型桌面 AI。文字与语音通过同一个 Main Agent 处理；控制台显示主对话历史和只读 Task 状态，设置窗口管理全局模型 API、语音、桌宠与工作目录；Profile 管理页独立配置人格、Memory 归属、Live2D 模型及动作表情。

## 当前集成边界

~~~mermaid
flowchart LR
  C["Console<br/>文字输入 · 历史 · Task 状态"] -->|IPC| E["Electron Main<br/>配置 · 进程 · 可见历史"]
  E -->|转发输入| P["Pet<br/>唯一文字/语音 WebSocket"]
  P <--> B["Python Main Agent<br/>Context · Memory · Task 工具"]
  E -->|启动与显式重启| B
  B --> T["TaskManager → Pi RPC"]
  P -->|交付回执| E
~~~

- 默认文字模式；未配置模型时仍能打开桌宠和设置。文字聊天不要求麦克风、语音 SDK 或语音模型资产。
- 用户显式开启麦克风才启用语音。语音整句经过输出设备播放完成后，Electron 写入历史、更新展示，Pet 再发送 ACK；中断句不进入历史。
- Main Agent 独占 Context、Memory 和 Task 业务编排。只有确认接收的用户消息及 `say_to_user` 交付内容进入桌面历史。
- Task 列表只显示标题和状态。创建、问答、暂停、恢复、取消、删除和重跑通过 Main Agent 完成；Memory 不提供外部管理 API 或 UI。

## 开发启动

需要 Node.js 22.19+、npm 与 [uv](https://docs.astral.sh/uv/)。macOS 是主要开发平台；Windows 打包与实机验证的边界见 [Windows 适配](docs/windows-platform-adaptation.md)。

在仓库根目录安装依赖：

~~~bash
uv sync --project backend --locked
npm ci --prefix pi-aurabot-task
npm ci --prefix pi-runtime
npm ci --prefix frontend
~~~

启动：

~~~bash
cd frontend
npm run dev
~~~

Electron Main 是唯一 Backend 进程 owner；开发入口启动 Electron/Vite，再由 Electron 启动 Python。Vite 使用 `127.0.0.1:6400`，Backend 使用 `127.0.0.1:6401`。不要另外启动第二个 Backend；端口被其他进程占用时应用报告错误，不终止其他进程。

首次运行自动创建 `<Electron userData>/desktop-v2/settings.json`。打开设置，填写 Main Agent 模型与密钥，先“保存”，再“应用”。Task/Pi 和语音可分别配置；人格和 Live2D 在 Profile 详情独立保存、应用。无需复制或维护仓库 YAML 配置。

`AURABOT_USER_DATA_DIR` 可指定隔离的数据根，此时直接使用该目录，不再追加 `desktop-v2`。Electron 将 JSON 路径通过 `AURABOT_CONFIG_PATH` 传给 Python；Python 只在启动时读取。

## 配置、数据与验证

- 全局设置保存/应用沿用 Backend 重启边界；Profile 人格/视觉保存与应用独立，在明确会话边界更换当前 Profile 资源，无需重启整个 Python 或其他 Task。
- 每个 Profile 的 Soul、Context、Memory、Qdrant、adaptor 和自有模型包位于独立目录。桌面交付历史保存在 `conversation-history.sqlite3`，按 Profile 分区、按游标分页；回执验证原 Profile/generation，历史不会回灌 Main Agent。
- 启动时迁移旧 Profile/Soul 与已保存编辑，SQLite backup 保留 WAL，Memory 在安全关闭条件下迁移；原数据和备份保留，不重置用户数据。详见 [Profile 与 Live2D](docs/profile-live2d.md)。

本地检查入口：

~~~bash
curl http://127.0.0.1:6401/health
curl http://127.0.0.1:6401/api/v1/readiness
~~~

`/health` 返回 `{"ok":true,"service":"backend"}`。Readiness 只检查本地生命周期与能力状态，不执行付费模型探测。

~~~bash
uv run --project backend python -m unittest discover -s backend/tests -t .
cd frontend
npm run typecheck
npm test
npm run build
npm run test:e2e
~~~

自动测试不等于真实麦克风、音频设备、STT/TTS 或模型 Provider 验收。实际验证结果与限制以 [本轮集成记录](docs/frontend-integration-work.md) 为准；语音安装见 [语音资产](docs/main-agent-speech-assets.md)，完整索引见 [文档](docs/README.md)。

2026-09-06 架构优化：旧 Electron 业务与 Python ShortTerm 实现已退役；Task 只对外提供状态 SSE。
实现和验证见 [架构优化记录](docs/architecture-optimization.md)。
