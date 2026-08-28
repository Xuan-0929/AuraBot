# AuraBot 当前开发状态

更新时间：2026-08-27

## 当前事实

| 范围 | 状态 |
| --- | --- |
| Desktop | Electron + Vue + TypeScript + Vite；Live2D、控制台和设置保留。 |
| Chat/Memory | 普通聊天、Memory follow-up、idle nudge 和持久化保留。 |
| Chat attachments | 新附件输入已关闭；历史消息仅保留只读附件名称，不再暴露本地路径或打开动作。 |
| Python Backend | lifespan 只启动 Task/Realtime/active-profile owner；Pet token 惰性激活单 Profile Main/Memory/Speech；health/readiness 零付费 probe。 |
| Backend speech | 生命周期、实时 mic/STT/VAD/EOU/TTS/ACK 边界已接入；默认禁用，只有显式配置且安装可选 SDK/模型资产后才可用。 |
| Python Memory | automatic lexical-first recall、300 ms vector degradation、五分钟 pending seal、统一 extraction/vector-delete job 与 active-Profile API 已实现。 |
| Task | Python Control Plane 与 Main Agent proposal/Task fact bridge 已实现；桌面 Task UI 仍不可用。 |
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

本轮完成 Main Agent V1 减法闭环：Runtime 保留 input/revision/context fence；Memory 使用
Profile 独立 SQLite 的 pending/job/item 真相与一个 active-only worker，不再使用 lease/outbox；
Gateway 按 token 串行激活 Profile。搜索 Observation 会触发 CONTINUE，
Task proposal durable 后以 Task fact 回流 Context。后续仍是 Electron surface、Artifact 与生产打包。

## 验证入口

~~~bash
uv run --project backend python -m unittest discover -s backend/tests -t .
cd frontend
npm run typecheck
npm run test:unit
npm test
npm run build
~~~

验收测试覆盖 silent/voice E2E、recall timeout/degrade/source filtering、seal/restart/retry、
active-Profile forget/retry API、readiness/exact health/lifespan、
search→CONTINUE 与 Task proposal→Task fact。真实 provider smoke 仍为显式 opt-in，默认
TestClient/static route 不调用付费 provider。固定 Pi CLI 版本仍精确为 0.84.2。
