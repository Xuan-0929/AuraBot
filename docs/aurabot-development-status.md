# AuraBot 当前开发状态

更新时间：2026-07-25

这份文档记录当前 `main` 的可复核工程状态和下一阶段入口。它不替代 PRD、模块设计或迁移路线。

## 当前基线

- 分支：`main`
- 工程基线：已包含 Python 长期记忆服务、短期记忆持久化与待总结片段可靠交付。
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
| Python Agent 能力 | 已有 Skill / Tool、Task、HarnessFactory、ReAct / PlanAndSolve Harness 与工具调用能力，但 Main Agent 尚未接入运行时请求路径。 |
| Python 长期记忆 | 已有 remember、recall、forget、improve、due、debug 与 health；SQLite 保存 episode、记忆、实体、关系和审计，Qdrant 提供本地向量索引。 |
| Python 短期记忆 | 按 profile/source 持久化消息和话题断点；达到断点数或上下文长度阈值后，原子生成最早完整待总结片段。 |
| 待总结片段交付 | 已有 pending、processing、completed、failed 状态，支持 claim、lease、ack、release、失败重试和重启后的过期 lease 恢复。 |
| TTS 调研 | dots.tts 已完成本地试验；CPU 生成不具备实时交互条件，因此不作为默认引擎。 |

## 关键边界

1. 当前 TypeScript core 是聊天、记忆和任务写入链路的事实来源；Python 不是并行第二套业务核心。
2. Python 后端的目标是唯一主运行时，但迁移必须按垂直链路逐步完成，详见 [后端迁移路线](backend-runtime-migration.md)。
3. 在迁移完成前，Electron JSON 配置与 Python YAML 配置分别服务于旧 TypeScript runtime 和新 Python runtime，不能假设二者自动同步。
4. 记忆模块只负责准备和可靠保存待总结片段，不自行总结或写入长期记忆；片段必须由 Main Agent 消费。
5. TaskManager 持久化、artifact registry、WebSocket hub、Main Agent 事件队列和 session 恢复仍未完成。

## 2026-07-25 开发记录：短期记忆可靠交付

- 将原短期记忆实现拆分为领域模型、策略、存储协议、同步 buffer 和 SQLite 存储。
- 按 PRD-v2 保留 Main Agent 的责任边界：Guard/Main Agent 打断点，达到阈值后记忆模块只生成 `SummaryRequest`。
- 待总结片段在生成后从工作区消息转移到持久化 segment，应用重启不会丢失。
- 消费端必须先 claim 并持有有效 lease；成功后 ack，失败时 release，达到最大尝试次数后进入 failed，可人工 requeue。
- profile 与 source_type 在读取和状态转换中均精确隔离。
- SQLite 文件使用私有权限并校验普通文件、symlink 和打开期间的文件身份。
- 旧 JSON 短期记忆仅通过隔离 sidecar 做一次性迁移，不复用旧记忆引擎。
- SQLite 内部实现按 schema、files、codec、legacy、delivery 拆分；测试按 store、delivery、buffer、legacy 拆分。
- 当前缺口是 Main Agent workflow、真实话题切换/总结模型、真实 embedding provider，以及 SQLite/Qdrant 索引同步重试。

## 下一阶段

1. 落地 Main Agent 最小闭环：单队列、单 session、recall、短期记忆、断点、片段总结决策、remember 和可靠 ack/release。
2. 接入真实话题切换、总结/抽取和 embedding provider，并建立记忆召回评估基线。
3. 增加 SQLite/Qdrant 索引同步 outbox、启动修复和 due 事件消费闭环。
4. 统一配置和后端生命周期管理，建立 HTTP 写接口与 WebSocket 事件，再让 Electron 按垂直切片切换到后端。
5. 将 task-manager 变为持久化、事件驱动的执行协调器；随后迁移现有 CLI runner 能力。

## 验证基线

- `cd frontend && npm run dev`：后端网关、Vite 和 Electron 同时启动。
- `curl http://127.0.0.1:6401/health`：网关返回 `ok: true`。
- `cd frontend && npm run typecheck`：验证前端类型。
- `cd frontend && npm test`：验证现有 TypeScript MVP。
- `cd frontend && npm run test:backend`：通过 uv 管理的 Python 环境验证完整后端测试集。
- `python -m unittest discover -s backend/tests -p 'test_short_term*.py'`：短期记忆 130 项测试通过。
- 后端完整测试集：359 项通过，2 项跳过；仍有一个既有 Qdrant 本地连接 `ResourceWarning` 待清理。
