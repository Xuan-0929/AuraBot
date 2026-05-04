# AuraBot GitHub 周报

周期：2026-04-28 至 2026-05-04  
仓库：`Xuan-0929/AuraBot`  
默认分支：`main`  
当前开发分支：`codex/aurabot-memory-skeleton`  
生成时当前 HEAD：`4deee48 Show console messages immediately`

## 本周概览

本周 AuraBot 从早期产品文档推进到可运行的桌面优先 MVP。GitHub 上已经形成了完整的产品文档、Electron 桌宠工程骨架、Live2D 桌面小人、主控制台聊天、记忆 v1、任务 v1、模型适配层和基础验证链路。

本周 GitHub 提交记录共 14 个 commit。按 `git log --since=2026-04-28 --numstat` 统计，提交记录覆盖 208 条文件变更记录，约 30,935 行新增、1,821 行删除。当前分支 `codex/aurabot-memory-skeleton` 已推送到远端，并且相对 `origin/main` 多 8 个开发 commit。GitHub 当前没有打开或关闭的 PR/Issue 记录。

本报告生成时，除已推送 commit 外，当前工作区还有一组待提交的子任务 runner 工作：25 个已跟踪文件有修改；不含本周报文件，另有 2 个 runner 相关新增未跟踪文件。已跟踪 diff 约 717 行新增、31 行删除，runner 新增文件合计 567 行。下面的“本轮待提交进展”单独标记，避免和当时已推送 GitHub 成果混淆。

## 已推送进展

### 1. 产品与技术文档落地

本周先完成了 AuraBot 的产品方向和工程边界整理，并把文档移动到 `docs/` 根目录，形成后续开发可以直接引用的基础材料：

| 文件 | 内容 |
| --- | --- |
| `docs/PRD-v1.md` | 产品需求和 MVP 边界 |
| `docs/concepts.md` | 核心概念说明 |
| `docs/TS.md` | 技术选型 |
| `docs/CA.md` | 架构/能力拆解 |
| `docs/TODO.md` | 分阶段任务清单 |
| `docs/aurabot-memory-structure.md` | 记忆结构设计 |
| `docs/aurabot-development-status.md` | 当前开发状态板 |

文档侧完成了一个重要收敛：AuraBot 不再只是传统聊天界面，而是明确转向“桌宠优先 + 控制台主线程 + 后台任务”的形态。

### 2. 桌宠优先 MVP 建成

`bff37ef Build AuraBot desktop pet MVP` 和后续提交搭起了核心应用：

- Electron + React + TypeScript + Vite + pnpm 工程骨架已可构建运行。
- 默认启动透明置顶的 Live2D 桌面小人窗口。
- 接入官方样例 `Hiyori`，Cubism Core 本地打包，启动时不依赖 CDN。
- 小人悬浮时展示 `Drag / Voice / Console` 控制。
- 控制台独立窗口按需打开，用于完整主聊天。
- 桌宠位置、设置、状态同步和任务完成气泡已接入。
- `artifacts/desktop-pet.png` 和 `artifacts/console-window.png` 已作为可视化产物保留。

这一步让项目具备了可以演示的第一屏，而不是只停留在文档或普通 Web 聊天 UI。

### 3. 主聊天、任务控制和消息体验补强

本周围绕控制台主线程做了多轮体验修正：

- `ca2ee99 Route main chat task controls by hint`：主聊天可以按 hint 命中特定任务，支持查询、打断、修改、启动。
- `8379c9f Fix console conversation scrolling`：修复控制台对话滚动体验。
- `94b368a Send console messages with Enter`：`Enter` 发送，`Ctrl+Enter` 换行。
- `4deee48 Show console messages immediately`：用户消息本地即时显示，不再等待 gate/chat 模型返回后才上屏。

当前控制台定位已经比较清楚：它是主聊天和任务控制面，不再额外堆任务按钮面板。

### 4. 模型适配层进入可配置状态

`f45cc9c Add configurable external model adapters` 和 `d75b06a Support system config and gate normalization` 让模型层从 deterministic fallback 往真实 provider 过渡：

- 已抽出 `gateModel` / `chatModel` 注入接口。
- 支持 OpenAI-compatible API provider。
- 支持 userData、系统级 JSON 配置和环境变量配置。
- gate 输出增加规范化，降低模型输出格式不稳定带来的风险。
- deterministic fallback 仍保留，用于本地测试、demo 和无 key 场景。

当前仍未实现流式回复、生成中断、provider 重试/超时可视化等生产化能力。

### 5. 记忆 v1 和任务 v1 打通 MVP

根据当前状态文档和代码结构，本周 MVP 已包含：

- SQLite 数据层：profiles、souls、sessions、messages、memory、tasks、event_logs 等表。
- 记忆 v1：支持 preference、event、follow_up 写入；支持摘要层和关系边召回。
- Gate/触发判断：可区分碎片输入、完整聊天、任务请求、任务控制和话题结束。
- 任务 v1：可从主聊天创建任务、查询状态、按 hint 控制、打断、修改、启动并完成 deterministic runner。
- 事件日志：gate、memory、task、chat 等关键路径已有记录。

这部分让 AuraBot 从“聊天壳”推进到“可记忆、可判断、可跟任务”的基础代理形态。

## 本轮待提交进展

本报告生成时，当前工作区正在推进 opt-in Codex 子任务 runner；它仍待本轮收尾提交，因此这里按“生成时待提交进展”记录，不混入上文的已推送成果。

核心变化包括：

- 新增 `src/core/tasks/codexTaskRunner.ts`：实现 `CodexTaskRunner` 和 `CliCodexAgentClient`，通过 `codex exec` 子进程执行任务，并通过 `--output-last-message` 读取最终结果。
- 新增 `tests/unit/core/tasks/codexTaskRunner.spec.ts`：覆盖 child Codex client 的参数传递、stdin prompt、输出文件读取，以及 runner 成功/失败状态写回。
- `src/shared/schemas/config.ts`、`src/core/bootstrap/loadConfig.ts`、`src/core/bootstrap/createServices.ts`：增加 `taskRunner` 配置，支持 `deterministic` / `codex` provider、Codex 命令、工作目录、sandbox、approval policy、model、timeout。
- `src/core/tasks/taskService.ts`：新增后台执行入口，任务启动后可立即进入执行态，并在 runner 异常时落 failed 状态。
- `src/main/registerIpc.ts`、`src/main/createWindow.ts`、`src/preload/index.ts`、`src/shared/contracts/ipc.ts`：增加任务变化和 assistant 消息广播，让子任务完成后能回流主聊天。
- `src/renderer/src/app/store.ts`、`src/renderer/src/features/tasks/TaskStatusOverlay.tsx`、`src/renderer/src/styles.css`：前端订阅主进程广播，任务条展示 latest progress。
- `src/core/storage/migrations.ts`、`src/core/storage/db.ts`：新增迁移能力，给旧任务表补 `session_id` 字段。
- README、中文 README、TODO、开发状态文档已同步说明 Codex 子任务 runner 的显式配置方式和边界。

这组改动直接验证了本次用户触发的真实场景：用户在主聊天说“帮我写一份 GitHub 周报”，AuraBot 创建任务后可以把任务派给本机 Codex 子 agent，等待子 agent 最终结果，再回写主聊天。

## 验证记录

现有 `docs/aurabot-development-status.md` 记录的最近一次完整验证结果如下：

| 检查 | 记录结果 |
| --- | --- |
| `corepack pnpm test` | 23 个 test files、74 个 tests 通过 |
| `corepack pnpm typecheck` | 通过 |
| `corepack pnpm build` | 通过 |
| `corepack pnpm test:e2e` | 11 个 e2e tests 通过 |
| Live2D 冷启动检查 | `dev:clean` 可见 Hiyori，`renderState=ready`，`motionGroup=Idle` |

本次生成周报时额外做了轻量检查：

- `gh pr list --repo Xuan-0929/AuraBot --state all --limit 20`：无 PR。
- `gh issue list --repo Xuan-0929/AuraBot --state all --limit 20`：无 Issue。
- `git diff --check`：当前 diff 未发现 whitespace error。

注意：本次周报任务没有重新执行完整 `pnpm test / typecheck / build / test:e2e`，因此上表完整验证结果来自仓库已有状态文档，而不是本次重新跑出的新日志。

## 当前风险与边界

| 风险/边界 | 影响 | 建议 |
| --- | --- | --- |
| Codex 子任务 runner 在报告生成时仍待提交 | 需要先完成完整验证和独立提交，才能作为远端分支能力宣传 | 本轮收尾提交并 push |
| 子任务只能失败式恢复 | 应用退出后，运行态任务会被标记为失联失败，但还不能自动 resume | 下一步设计 Codex session resume 或 artifact 对账 |
| artifact 状态仍较粗 | 主聊天能看到 Codex JSONL 进度、命令和文件路径，但还没有结构化 artifact 清单 | 继续丰富 artifact 回流和失败上下文 |
| 模型回复非流式且不可中断 | 对话体验仍不像成熟桌面 agent | 优先接流式回复和 in-flight generation interrupt |
| 记忆 v1 仍是规则召回 | 长期陪伴感和联想能力有限 | 引入 embedding/LLM 辅助的候选召回、合并和更新 |
| GitHub 没有 PR/Issue 流程 | 开发进展都靠 commit 和本地文档记录 | 后续可以用 PR 模板和 issue milestone 管理阶段目标 |

## 下周建议

1. 基于本轮 Codex 子任务 runner 提交继续打磨真实任务闭环，形成 AuraBot “主线程创建任务，子 agent 执行任务，最终回流主聊天”的稳定能力。
2. 设计真正的子任务 resume 和 artifact 对账，让失联任务不只能失败式恢复。
3. 设计 `TaskRegistry` 的真实任务类型、输入输出协议和 artifact 回流格式，让“写周报”这类任务可沉淀为可复用任务类型。
4. 做模型层生产化：流式回复、用户打断、provider 错误提示、重试和事件日志。
5. 做记忆 v2：重复记忆合并、旧记忆更新、固定对话集评估、记忆调试视图。
6. 整理 GitHub 协作面：开 PR、挂 milestone，把桌宠行为层、模型层、任务执行 v2、记忆 v2 分成可审查的开发单元。

## 可对外摘要

本周 AuraBot 已从产品文档推进到可运行的桌面优先 AI 伴侣 MVP：完成 Electron/React 工程骨架、Live2D 桌宠窗口、独立控制台主聊天、SQLite 记忆与任务基础、模型适配层、任务控制流和 e2e 验证。当前开发重点正在转向真实代理执行：本地已接入 opt-in Codex 子任务 runner，使 AuraBot 能把主聊天创建的任务委派给本机 Codex 子 agent，广播执行进度，在打断时取消子进程，在启动时对账失联运行态任务，并在任务完成后把结果回流主聊天。下一步重点是补齐真正 resume / 任务 artifact 协议，并把记忆和模型层从 MVP 打磨到可长期运行。
