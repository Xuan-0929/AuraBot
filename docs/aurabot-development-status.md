# AuraBot 当前开发状态

更新时间：2026-05-04

这份文档记录当前工作树已经完成的工程事实、最近一次验证结果，以及下一步最应该推进的任务。它是执行状态板，不替代 PRD，也不替代 MVP 计划。

## 当前产品形态

AuraBot 当前已经从“传统聊天界面”收敛为桌宠优先形态：

1. 默认启动一个透明、置顶的小型桌面 Live2D 小人窗口，平时只展示小人本体。
2. 鼠标悬浮到小人上时，小人下方浮现半透明 `Drag / Voice / Console` 控制。
3. 用户点击控制台按钮后，打开或聚焦独立控制台视窗进行完整主线程对话。
4. 任务创建、查询、打断、修改、启动都通过控制台里的主聊天线程完成。
5. 控制台只展示轻量任务状态条，不提供任务控制按钮。
6. 任务完成和桌宠语音回复可以通过小人气泡临时浮现。
7. 记忆系统当前是本地 deterministic 的“分层摘要 + 图状联想” MVP。

## 已完成

| 领域 | 状态 | 说明 |
| --- | --- | --- |
| Electron 项目骨架 | 已完成 | `Electron + React + TypeScript + Vite + pnpm` 已可构建和运行。 |
| 双窗口桌宠壳层 | 已完成 | `createPetWindow()` 默认创建桌面小人，`openConsoleWindow()` 按需打开控制台。 |
| Live2D 真实模型 | 已完成 | 已接入官方样例 `Hiyori`，Cubism Core 本地打包，不依赖启动时 CDN。 |
| Live2D 桌宠交互层 | 部分完成 | 悬浮控制条、拖拽按钮、控制台按钮、点击穿透设置、任务完成气泡、位置持久化、状态 motion 映射已接入。 |
| SQLite 数据层 | 已完成 | 已有 profiles、souls、sessions、messages、memory、tasks、event_logs 等表。 |
| 单主聊天线程 | 已完成 MVP | 主 session 持久化消息，控制台负责完整对话。 |
| 模型适配层 | 部分完成 | 已有 `gateModel` / `chatModel` 注入接口、OpenAI-compatible provider、userData/系统级 JSON 配置、env 配置和 deterministic fallback；还缺流式与中断。 |
| Gate/触发判断 | 已完成 MVP | 可判断碎片输入等待、完整输入回复、话题是否结束、聊天/任务/任务控制分类。 |
| 记忆 v1 | 已完成 MVP | 支持 preference、event、follow_up 写入，摘要层和关系边召回。 |
| 任务 v1 | 已完成 MVP | 可从主聊天创建任务、按聊天 hint 命中特定任务、查询状态、打断、修改、启动并完成 deterministic runner。 |
| 事件日志 | 部分完成 | 已写入 gate、memory、task、chat 关键日志；还没有调试视窗或日志开关。 |
| E2E 演示流 | 已完成 MVP | 桌宠启动、悬浮控制开控制台、桌宠设置同步、位置持久化、状态同步、记忆、碎片等待、任务控制路径都有 e2e 覆盖。 |

## 最近一次验证记录

最近一次完整验证在 2026-05-04 当前工作树完成：

| 命令/检查 | 结果 |
| --- | --- |
| `corepack pnpm test` | 20 个 test files、56 个 tests 通过 |
| `corepack pnpm typecheck` | 通过 |
| `corepack pnpm build` | 通过 |
| `corepack pnpm test:e2e` | 10 个 e2e tests 通过 |
| Live2D 冷启动检查 | `dev:clean` 可见渲染 Hiyori，`renderState=ready`，`motionGroup=Idle` |

可视化产物：

1. 桌面小人截图：`artifacts/desktop-pet.png`
2. 控制台截图：`artifacts/console-window.png`

## 当前边界

1. 模型角色默认仍走 deterministic fallback；外部模型调用需要本地 `aurabot.config.json` / `config.json`、macOS 系统级 `/Library/Application Support/AuraBot/config.json`，或环境变量。
2. 模型回复目前是非流式；还没有用户新消息打断 in-flight generation。
3. 记忆抽取和联想是规则实现，没有 embedding，也没有 LLM 参与候选边判断。
4. 任务 runner 是 deterministic，占位验证任务生命周期，不是真实工具执行代理。
5. Live2D 状态行为当前复用 Hiyori 现有 motion group，还没有定制表情资源或更细的动画状态机。
6. 语音输入当前走 Chromium 原生 `SpeechRecognition`，运行时不支持时只显示轻提示；还没有稳定 STT adapter。
7. 还没有 Profile 切换 UI、定时任务、桌面通知、打包发布、开机自启。

## 下一步推荐

### 1. Live2D 桌宠行为层

目标：让小人不只是挂在桌面，而是能表达 AuraBot 当前状态。

建议任务：

1. 继续细化状态动作：为 `idle / listening / thinking / replying / tasking` 选择更贴切的 motion 和后续 expression 资源。
2. 继续细化点击穿透：小人主体可穿透，悬浮控制区仍可稳定点击。
3. 接入稳定 STT adapter，替换当前浏览器原生语音识别 MVP。
4. 为真正的主动提醒/主动对话补后台触发入口，并复用当前气泡通道。
5. 增加开发期诊断面板，展示 Live2D `renderState`、motion、窗口 bounds、click-through 状态。

### 2. 真实模型适配层

目标：把已接入的可配置模型调用打磨成可长期运行的模型层。

建议任务：

1. 增加流式回复，把 `replying` 状态和消息逐字/逐段展示接起来。
2. 支持用户新消息打断当前强模型生成。
3. 为 provider 调用增加重试、超时、失败事件日志和可见错误提示。
4. 在控制台或设置视窗暴露当前模型连接状态。
5. 为 gate 模型输出增加线上样本日志，后续评估“是否抢话/是否漏接/任务误判”。

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
