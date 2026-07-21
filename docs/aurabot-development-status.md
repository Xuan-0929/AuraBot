# AuraBot 当前开发状态

更新时间：2026-07-17

这份文档记录当前 `main` 的可复核工程状态和下一阶段入口。它不替代 PRD、模块设计或迁移路线。

## 当前基线

- 分支：`main`
- 已同步提交：`67e397a`（2026-07-15）
- 桌面端：Electron + Vue + TypeScript + Vite。
- 后端：Python FastAPI 单进程网关，开发端口 `6401`。
- 开发入口：先从仓库根目录执行 `uv sync --project backend --locked`，再从 `frontend/` 执行 `npm run dev`；需要提供 `config.yaml`。

## 已落地

| 范围 | 当前事实 |
| --- | --- |
| 桌宠与控制台 | Live2D Hiyori 桌宠、悬浮控制、快速聊天、控制台、设置页和日语界面已可运行。 |
| TypeScript MVP 业务 | Electron main process 中仍承载聊天编排、模型调用、记忆、任务控制、任务 CLI runner 和主动提醒。 |
| Python 网关 | `/health`、`/services`、聊天历史、任务列表和调试只读接口已提供。 |
| Python 配置 | `backend.main` 在启动时读取根目录 `config.yaml`，支持环境变量展开。 |
| Python Agent 脚手架 | 已有 Skill / Tool 数据模型、SQLite 辅助层、Task、HarnessFactory 和 Harness 类型，但未接入运行时请求路径。 |
| Python 记忆服务 | 已有 remember、recall、forget、improve、due 与 debug 服务及测试；尚未由 `backend.main` 挂载或由主 Agent 调用。 |
| TTS 调研 | dots.tts 已完成本地试验；CPU 生成不具备实时交互条件，因此不作为默认引擎。 |

## 关键边界

1. 当前 TypeScript core 是聊天、记忆和任务写入链路的事实来源；Python 不是并行第二套业务核心。
2. Python 后端的目标是唯一主运行时，但迁移必须按垂直链路逐步完成，详见 [后端迁移路线](backend-runtime-migration.md)。
3. 在迁移完成前，Electron JSON 配置与 Python YAML 配置分别服务于旧 TypeScript runtime 和新 Python runtime，不能假设二者自动同步。
4. TaskManager、artifact registry、WebSocket hub、主 Agent 队列和断电恢复均尚未完成。

## 下一阶段

1. 统一配置和后端生命周期管理。
2. 落地 Main Agent 最小闭环：单队列、单 session、短期记忆、长期记忆调用和持久化测试。
3. 建立 HTTP 写接口与 WebSocket 事件，再让 Electron 按垂直切片切换到后端。
4. 将 task-manager 变为持久化、事件驱动的执行协调器；随后迁移现有 CLI runner 能力。

## 验证基线

- `cd frontend && npm run dev`：后端网关、Vite 和 Electron 同时启动。
- `curl http://127.0.0.1:6401/health`：网关返回 `ok: true`。
- `cd frontend && npm run typecheck`：验证前端类型。
- `cd frontend && npm test`：验证现有 TypeScript MVP。
- `cd frontend && npm run test:backend`：通过 uv 管理的 Python 环境验证完整后端测试集。
