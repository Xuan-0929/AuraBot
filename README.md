# AuraBot

AuraBot 是一个 desktop-first 的陪伴型 AI 项目。默认界面是常驻桌面的 Live2D
角色，控制台提供普通聊天、Memory、模型与桌宠设置。

## 当前可用能力

| 范围 | 当前状态 |
| --- | --- |
| Desktop | Electron + Vue + TypeScript + Vite，可运行桌宠、控制台和设置窗口。 |
| Live2D | 内置 Hiyori 示例模型，支持拖动、点击穿透、光标跟随与状态动作。 |
| Chat | 普通文字聊天、pet quick chat、模型调用和消息持久化。 |
| Memory | TypeScript MVP 与保留的 Python Memory 实现、follow-up 与 idle nudge。 |
| Backend | Python Task REST/SSE 垂直切片已实现；health 契约保持不变。 |
| Task | 两表同步 SQLite、固定五槽 FIFO、Pi RPC、立即 waiting 回收、暂停/恢复/取消和业务事件已实现；Electron/Main Agent 尚未接入。 |

桌面 Task 型输入仍只会收到固定迁移回复，因为 Electron/Main Agent 接入不在本轮。
后端 API 可独立创建并运行 Task，但桌面不会暗示该 surface 已经可用。

## 架构状态

~~~mermaid
flowchart LR
  DESKTOP["Electron / Vue<br/>Chat · Memory · Live2D"] --> HEALTH["Python FastAPI<br/>health + Task REST/SSE"]
  HEALTH --> TM["Python TaskManager<br/>Control Plane"]
  TM --> PI["Pi RPC processes<br/>Execution Plane"]
~~~

未来 Task 架构、职责边界和第二轮范围以
[Pi Task Runtime](docs/modules/task-agent.md) 为唯一事实源。

## Quick Start

要求：

1. Node.js 22.19+ 与 npm（Pi 0.84.2 要求 Node >=22.19.0）。
2. [uv](https://docs.astral.sh/uv/)。
3. macOS 是主要开发平台；Windows 10/11 x64 保留打包目标。

安装：

~~~bash
uv sync --project backend --locked
npm ci --prefix pi-aurabot-task
npm ci --prefix pi-runtime
cd frontend
npm install
~~~

创建 Backend 配置：

~~~bash
cp config.example.yaml config.yaml
~~~

启动桌面开发环境：

~~~bash
cd frontend
npm run dev
~~~

npm run dev 会启动 Python Backend、Vite 和 Electron。也可在
frontend 目录分别运行 npm run dev:backend、npm run dev:frontend 和
npm run dev:desktop。

Backend 组合入口仍是 backend/main.py。启动后固定健康检查：

~~~bash
curl http://127.0.0.1:6401/health
~~~

响应：

~~~json
{"ok":true,"service":"backend"}
~~~

## Task Runtime 配置

仓库根配置的新 `task` 段指定 AuraBot user data、私有 `pi-runtime` 路径、Node 和
默认 timeout。并发固定为五且不可配置；旧 `task.concurrency_limit` 会导致启动失败。
`pi-runtime` 精确锁定 Pi；`pi-aurabot-task` 提供标准
`ask_main_agent` Extension。二者都不是 Electron runner 或 Node Worker。
HTTP 创建请求省略 timeout 时使用 YAML 默认值；显式 null 或 `1..86400` 之外的值
会被拒绝。

## 其他配置

Python Backend 读取仓库根目录 config.yaml；AURABOT_CONFIG_PATH 可覆盖路径。
Electron 的模型与桌宠设置仍使用 userData 下的 aurabot.config.json 或
config.json。旧 taskRunner/Codex/Claude/Hermes 字段不会被读取，也不会被主动
重写。

当前 Electron 模型角色：

- gateModel：轻量分类与回复时机。
- chatModel：生成用户可见回复。
- memoryModel：记忆抽取与合并。

## 验证

~~~bash
uv run --project backend python -m unittest discover -s backend/tests -t .
cd frontend
npm run typecheck
npm run test:unit
npm test
npm run build
~~~

更多文档见 [docs/README.md](docs/README.md)。
