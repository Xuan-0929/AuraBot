# AuraBot 当前开发状态

更新时间：2026-05-04

这份文档记录当前工作树已经完成的工程事实、最近一次验证结果，以及下一步最应该推进的任务。它是执行状态板，不替代 PRD，也不替代 MVP 计划。

## 当前产品形态

AuraBot 当前已经从“传统聊天界面”收敛为桌宠优先形态：

1. 默认启动一个透明、置顶的小型桌面 Live2D 小人窗口。
2. 用户点击小人后，打开独立控制台视窗进行完整主线程对话。
3. 任务创建、查询、打断、修改、启动都通过控制台里的主聊天线程完成。
4. 控制台只展示轻量任务状态条，不提供任务控制按钮。
5. 记忆系统当前是本地 deterministic 的“分层摘要 + 图状联想” MVP。

## 已完成

| 领域 | 状态 | 说明 |
| --- | --- | --- |
| Electron 项目骨架 | 已完成 | `Electron + React + TypeScript + Vite + pnpm` 已可构建和运行。 |
| 双窗口桌宠壳层 | 已完成 | `createPetWindow()` 默认创建桌面小人，`openConsoleWindow()` 按需打开控制台。 |
| Live2D 真实模型 | 已完成 | 已接入官方样例 `Hiyori`，Cubism Core 本地打包，不依赖启动时 CDN。 |
| SQLite 数据层 | 已完成 | 已有 profiles、souls、sessions、messages、memory、tasks、event_logs 等表。 |
| 单主聊天线程 | 已完成 MVP | 主 session 持久化消息，控制台负责完整对话。 |
| Gate/触发判断 | 已完成 MVP | 可判断碎片输入等待、完整输入回复、聊天/任务/任务控制分类。 |
| 记忆 v1 | 已完成 MVP | 支持 preference、event、follow_up 写入，摘要层和关系边召回。 |
| 任务 v1 | 已完成 MVP | 可从主聊天创建任务、查询状态、打断、修改、启动并完成 deterministic runner。 |
| 事件日志 | 部分完成 | 已写入 gate、memory、task、chat 关键日志；还没有调试视窗或日志开关。 |
| E2E 演示流 | 已完成 MVP | 桌宠启动、点击开控制台、记忆、碎片等待、任务控制路径都有 e2e 覆盖。 |

## 最近一次验证记录

最近一次完整验证在 2026-05-04 当前工作树完成：

| 命令/检查 | 结果 |
| --- | --- |
| `corepack pnpm test` | 9 个 test files、22 个 tests 通过 |
| `corepack pnpm typecheck` | 通过 |
| `corepack pnpm build` | 通过 |
| `corepack pnpm test:e2e` | 6 个 e2e tests 通过 |
| Live2D canvas 检查 | `renderState=ready`，WebGL 非透明像素 `20780` |

可视化产物：

1. 桌面小人截图：`artifacts/desktop-pet.png`
2. 控制台截图：`artifacts/console-window.png`

## 当前边界

1. Chat/Gate/Response 仍是 deterministic MVP，不是真实 LLM 对话能力。
2. 记忆抽取和联想是规则实现，没有 embedding，也没有 LLM 参与候选边判断。
3. 任务 runner 是 deterministic，占位验证任务生命周期，不是真实工具执行代理。
4. Live2D 还没有根据状态驱动表情和动作，只完成真实模型挂载、缩放、点击打开控制台。
5. 还没有设置视窗、Profile 切换 UI、定时任务、桌面通知、打包发布、开机自启。

## 下一步推荐

### 1. Live2D 桌宠行为层

目标：让小人不只是挂在桌面，而是能表达 AuraBot 当前状态。

建议任务：

1. 将 `idle / listening / thinking / replying / tasking` 映射到 Live2D motion 或 expression。
2. 区分拖拽小人和点击小人，避免拖动时误开控制台。
3. 保存桌宠窗口位置，下次启动回到用户放置的位置。
4. 控制台已存在时点击小人只 focus，不重复创建窗口。
5. 为后续主动提醒预留轻量气泡或系统通知入口。

### 2. 真实模型适配层

目标：把 deterministic chat 替换成可配置模型调用，但保留本地测试稳定性。

建议任务：

1. 新增 provider 配置：`API Base URL`、`API Key`、`chatModel`、`gateModel`、启用开关。
2. 把 `gateModel` 和 `chatModel` 做成可注入接口。
3. 保留 deterministic adapter 作为测试和无 key fallback。
4. 支持流式回复和用户新消息打断生成。
5. 在控制台或设置视窗暴露当前模型连接状态。

### 3. 记忆 v2

目标：让 memory 更像联想，而不只是规则命中。

建议任务：

1. 用 LLM 或 embedding 替换当前轻量概念抽取层。
2. 增加重复记忆合并和旧记忆更新策略。
3. 为时间驱动记忆增加到期前后触发机制。
4. 增加记忆调试视图：本轮写入了什么、召回了什么、沿哪些边跳转。
5. 构造固定对话集，比较“关键词召回”和“图状联想召回”的差异。

### 4. 任务执行 v2

目标：从任务状态占位进入真实可执行代理。

建议任务：

1. 设计 `TaskRegistry` 的真实任务类型和输入输出协议。
2. 接入至少一个实际工具型任务，例如文件整理、网页搜集或摘要生成。
3. 明确任务 artifact 写入和回流主线程的格式。
4. 支持任务取消信号贯穿 runner，而不是只改状态。
5. 保持控制面在主聊天线程，不新增任务控制按钮面板。

### 5. 文档和交付整理

目标：让项目可以被继续开发、评审和 push。

当前状态：

1. `README.md` 已补，包含本地安装、运行、验证、截图路径、当前边界和下一步。
2. `artifacts/desktop-pet.png` 与 `artifacts/console-window.png` 已确认可以随本次提交进入仓库。
3. push 前仍建议再跑一轮 fresh `test / typecheck / build / test:e2e`。

## Push 建议

当前先不要自动 push。建议先 review 本状态文档和 MVP 计划更新：

1. 如果认可当前形态，就提交并 push 当前分支。
2. 如果要先调整下一步优先级，先改文档再 push。
3. 本次截图已确认可以提交，push 前需要把 `artifacts/desktop-pet.png` 和 `artifacts/console-window.png` 一起 stage。
