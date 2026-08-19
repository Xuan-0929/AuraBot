# AuraBot 当前开发状态

更新时间：2026-08-19

## 当前事实

| 范围 | 状态 |
| --- | --- |
| Desktop | Electron + Vue + TypeScript + Vite；Live2D、控制台和设置保留。 |
| Chat/Memory | 普通聊天、Memory follow-up、idle nudge 和持久化保留。 |
| Python Backend | health-only FastAPI；GET /health 返回 ok/service。 |
| Python Memory | backend/memory 与短期记忆存储保留。 |
| Task | 不可用；只返回固定迁移提示。 |
| 旧执行面 | Python Harness/MCP/Task runtime 与 TypeScript Task runners/UI 已删除。 |
| Pi Runtime | 目标已确定，尚未实现。 |

## 架构边界

Task 未来采用 Python Control Plane + 一 Task 一 Pi RPC 进程；唯一事实源是
[Pi Task Runtime](modules/task-agent.md)。当前仓库不包含 Pi dependency、Worker、
新 TaskManager、RPC DTO、Task API 或 Task UI。

## 下一阶段

第二轮从 Task 产品模型、状态持久化和 Pi RPC 进程监督重新设计。它不得复用旧
Harness、ToolGroup、Capability、MCP gateway 或 TypeScript runner。

## 验证入口

~~~bash
uv run --project backend python -m unittest discover -s backend/tests -t .
cd frontend
npm run typecheck
npm run test:unit
npm test
npm run build
~~~

2026-08-19 本轮验证结果：Backend 176 个 unittest 通过；Frontend 54 个
Vitest 文件、160 个测试通过；typecheck 与 production build 通过；3 条 Electron
E2E 通过。真实 Uvicorn smoke 的 OpenAPI 仅包含 `/health`。Build 仍只有既存的
Pixi 动态/静态混合导入 chunk warning。

当前完成口径是“旧执行面已清除、Pi Runtime 尚未实现”，不是 Task 功能恢复。
