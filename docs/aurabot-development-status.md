# AuraBot 当前开发状态

更新时间：2026-08-20

## 当前事实

| 范围 | 状态 |
| --- | --- |
| Desktop | Electron + Vue + TypeScript + Vite；Live2D、控制台和设置保留。 |
| Chat/Memory | 普通聊天、Memory follow-up、idle nudge 和持久化保留。 |
| Chat attachments | 新附件输入已关闭；历史消息仅保留只读附件名称，不再暴露本地路径或打开动作。 |
| Python Backend | health 与版本化 Task REST/SSE 已实现，Task 生命周期由 lifespan 管理。 |
| Python Memory | backend/memory 与短期记忆存储保留。 |
| Task | Python 后端垂直切片已实现；Electron/Main Agent 尚未接入，桌面入口仍不可用。 |
| 旧执行面 | Python Harness/MCP/Task runtime 与 TypeScript Task runners/UI 已删除。 |
| Pi Runtime | Pi 0.84.2、Python Control Plane 与本地 Extension Package 已实现并通过 offline smoke。 |

Electron SQLite 的全新安装与旧版本升级现在共享同一 Main Session schema：不存在
`kind`、`parent_session_id`、`depth` 或 `session_links`。旧 Task 磁盘目录在数据库
migration 提交后独立、幂等清理；部分删除不可回滚，失败会阻止启动并在下次重试。

## 架构边界

Task 采用 Python Control Plane + 一 Task 一 Pi RPC 进程；唯一事实源是
[Pi Task Runtime](modules/task-agent.md)。本轮只恢复可独立验证的 Backend surface，
不会复活 Electron Task repository/runner/UI。

## 本轮与后续

本轮已经实现产品模型、Python SQLite、FIFO/并发、Pi RPC Supervisor、TaskManager、
REST/SSE 与 offline smoke。后续才接 Main Agent、Electron、Artifact 和生产打包。

## 验证入口

~~~bash
uv run --project backend python -m unittest discover -s backend/tests -t .
cd frontend
npm run typecheck
npm run test:unit
npm test
npm run build
~~~

2026-08-20 最终验证结果：Backend 230 个 unittest 通过（包含真实 Pi offline
smoke）；Pi Extension Package 5 个 Node 测试与 TypeScript typecheck 通过，固定 CLI
版本精确为 0.84.2；Frontend 54 个 Vitest 文件、160 个测试、typecheck 与 production
build 通过。真实 Uvicorn health 响应精确，OpenAPI 只包含 `/health` 与版本化
`/api/v1/tasks*` 路径。Build 仍只有既存 Pixi 混合导入 chunk warning。
