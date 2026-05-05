# AuraBot 当前开发状态

更新时间：2026-05-05

这份文档记录当前任务系统分支已经完成的工程事实、最近一次验证结果，以及下一步最应该推进的任务。它是执行状态板，不替代 PRD，也不替代 MVP 计划。

当前工作分支：`codex/aurabot-task-system`

当前任务系统提交：`ac6afd9 feat: refine task chat flow`

分支边界：这一侧用于继续推进任务系统、主聊天任务控制、任务结果回流、任务状态展示和 gate/task 判定。记忆系统后续大改应继续放在记忆分支或独立工作树，避免和 `chatOrchestrator`、模型配置、memory service 等共享文件互相抢修改权。

## 当前产品形态

AuraBot 当前已经从“传统聊天界面”收敛为桌宠优先形态：

1. 默认启动一个透明、置顶的小型桌面 Live2D 小人窗口，平时只展示小人本体。
2. 鼠标悬浮到小人上时，小人下方浮现半透明 `Drag / Voice / Console` 控制。
3. 用户点击控制台按钮后，打开或聚焦独立控制台视窗进行完整主线程对话。
4. 控制台输入 `Enter` 直接发送，`Ctrl+Enter` 换行；用户消息和输入框清空都先本地即时完成，gate/模型结果随后回填。
5. 任务创建、查询、打断、修改、启动都通过控制台里的主聊天线程完成。
6. 控制台只展示轻量任务状态条，不提供任务控制按钮。
7. 任务完成和桌宠语音回复可以通过小人气泡临时浮现。
8. 记忆系统当前是“分层摘要 + 图状联想 + 可选 memoryModel 增强”的 MVP；这一分支只把它当作当前底座，不继续扩展记忆能力。

## 已完成

| 领域 | 状态 | 说明 |
| --- | --- | --- |
| Electron 项目骨架 | 已完成 | `Electron + React + TypeScript + Vite + pnpm` 已可构建和运行。 |
| 双窗口桌宠壳层 | 已完成 | `createPetWindow()` 默认创建桌面小人，`openConsoleWindow()` 按需打开控制台。 |
| Live2D 真实模型 | 已完成 | 已接入官方样例 `Hiyori`，Cubism Core 本地打包，不依赖启动时 CDN。 |
| Live2D 桌宠交互层 | 部分完成 | 悬浮控制条、拖拽按钮、控制台按钮、点击穿透设置、任务完成气泡、位置持久化、状态 motion 映射已接入。 |
| SQLite 数据层 | 已完成 | 已有 profiles、souls、sessions、messages、memory、tasks、event_logs 等表。 |
| 单主聊天线程 | 已完成 MVP | 主 session 持久化消息，控制台负责完整对话；用户消息乐观上屏，不再等待 gate/chat 模型返回。 |
| 模型适配层 | 部分完成 | 已有 `gateModel` / `chatModel` / `memoryModel` 注入接口、OpenAI-compatible provider、userData/系统级 JSON 配置、env 配置和 deterministic fallback；还缺流式与中断。 |
| Gate/触发判断 | 已完成 MVP | 可判断碎片输入等待、完整输入回复、话题是否结束、聊天/任务/任务控制分类；已放宽直接产出型任务判定，`做一份读书笔记`、`写一份GitHub周报` 等不需要“帮我”前缀也会进入任务流。 |
| 记忆 v1/v2 写入层 | 部分完成 | 支持 preference、event、follow_up 写入，摘要层和关系边召回；显式配置 `memoryModel` 后可用 API 做结构化记忆抽取、重复记忆合并和候选关系边判断。 |
| 任务 v1 | 已完成 MVP | 可从主聊天创建任务、按聊天 hint 命中特定任务、查询状态、打断、修改、启动；默认 deterministic runner，显式配置后可调用本机 Codex 子任务 runner。任务完成后可把结果文件作为消息附件回流主聊天，并提供“打开文件 / 所在文件夹”操作。 |
| 任务体验文案 | 已完成第一版 | 任务创建、查询、完成、失败、重复开始和状态条都已去掉内部状态词，改成伴侣兼助手式中文回复。 |
| 记忆调试视图 | 已完成第一版 | 控制台 `Memory Debug` 面板可查看当前 memory nodes、edges、summaries 计数和最近 memory event。 |
| 事件日志 | 部分完成 | 已写入 gate、memory、task、chat 关键日志；已有 memory debug 读取面板，还缺更完整的日志开关和路径可视化。 |
| E2E 演示流 | 已完成 MVP | 桌宠启动、悬浮控制开控制台、桌宠设置同步、位置持久化、状态同步、记忆、碎片等待、任务控制路径都有 e2e 覆盖。 |

## 最近一次验证记录

最近一次完整验证在 2026-05-05 当前任务系统分支完成：

| 命令/检查 | 结果 |
| --- | --- |
| `corepack pnpm test` | 30 个 test files、104 个 tests 通过 |
| `corepack pnpm typecheck` | 通过 |
| `corepack pnpm build` | 通过 |
| `corepack pnpm test:e2e` | 12 个 e2e tests 通过 |
| `git diff --check` | 通过，无 whitespace error |
| 外部 gate UI 烟测 | 使用系统级外部 gate 配置，临时 deterministic task runner；`做一份读书笔记` 和 `写一份GitHub周报` 都进入 `Tasking` 并创建任务状态卡。 |

可视化产物：

1. 桌面小人截图：`artifacts/desktop-pet.png`
2. 控制台截图：`artifacts/console-window.png`

## 当前边界

1. 模型角色默认仍走 deterministic fallback；外部模型调用需要本地 `aurabot.config.json` / `config.json`、macOS 系统级 `/Library/Application Support/AuraBot/config.json`，或环境变量。
2. 用户消息已经即时显示，但模型回复目前仍是非流式；还没有用户新消息打断 in-flight generation。
3. 记忆写入层可由 `memoryModel` 接管抽取、合并和候选边判断；召回层也可由 `memoryModel` 生成语义检索词、目标类型和重排 seed node。到期 `follow_up` 可在下一次主聊天回复中触发并标记完成；还没有后台 scheduler 或 embedding。
4. 任务 runner 可显式配置为本机 Codex 子任务执行器，能广播 JSONL 子任务进度，在主聊天打断时取消子进程，并在启动时把失联运行态任务标记为失败；完成后的文件结果已可作为主聊天附件回流并打开，还没有真正 resume 和结构化 artifact registry。
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

1. 增加流式回复，把 `replying` 状态和消息逐字/逐段展示接起来；用户消息即时显示已完成。
2. 支持用户新消息打断当前强模型生成。
3. 为 provider 调用增加重试、超时、失败事件日志和可见错误提示。
4. 在控制台或设置视窗暴露当前模型连接状态。
5. 为 gate 模型输出增加线上样本日志，后续评估“是否抢话/是否漏接/任务误判”。

### 3. 记忆 v2

目标：让 memory 更像联想，而不只是规则命中。

建议任务：

1. 已完成第一步：显式配置 `memoryModel` 后，可用 LLM 替换记忆抽取，并保留 deterministic fallback。
2. 已完成第一步：写入队列可通过 merge planner 更新重复记忆，而不是只按完全相同 content 去重。
3. 已完成第一步：候选关系边可通过 relation planner 由 API 判断，并限制 target node 必须来自已有节点。
4. 已完成第一步：召回阶段可通过 recall planner 获取 query terms、target types 和 ranked node ids，减少只靠关键词命中。
5. 已完成第一步：到期 `follow_up` 会在下一次主聊天回复中提醒，并在触发后标记为 `completed`。
6. 下一步：为时间驱动记忆增加后台 scheduler 和桌宠气泡主动触发。
7. 已完成第一版：控制台 `Memory Debug` 面板可查看 nodes、edges、summaries 计数、当前节点、关系边和最近 memory event。
8. 下一步：增强调试视图，展示本轮召回 seed、图扩散路径和模型 planner 原始输出。
9. 下一步：构造固定对话集，比较“关键词召回”和“图状联想召回”的差异。

### 4. 任务执行 v2

目标：从任务状态占位进入真实可执行代理。

建议任务：

1. 已完成：opt-in Codex 子任务 runner 支持创建任务后后台把 task prompt 交给本机 `codex exec`，实时广播 JSONL 进度，查询时显示最近进展，打断时取消子进程，并把最终结果回流主聊天。
2. 已完成：任务完成消息可以带文件附件，主聊天中展示文件名并提供“打开文件 / 所在文件夹”，不直接暴露原始路径。
3. 已完成：任务状态条用人话展示 `待开始 / 处理中 / 完成 / 没跑成 / 已停下`，并清理 `Codex 子任务`、`queued`、`completed` 等内部词。
4. 已完成：任务 gate 覆盖直接产出型请求，`做一份读书笔记`、`写一份GitHub周报` 和 `帮我写一份实践论的读书笔记` 都会进入任务流；外部轻量 gate 误判时会被本地 `local-task-guard` 拉回任务。
5. 已完成：启动时会把上次遗留的 running/waiting 任务标记为失联失败，避免永久卡在运行态。
6. 下一步：设计 `TaskRegistry` 的真实任务类型、输入输出协议和结构化 artifact registry。
7. 下一步：设计真正 resume 策略，确保应用重启后能恢复或准确解释子任务状态。
8. 继续保持控制面在主聊天线程，不新增任务控制按钮面板。

### 5. 文档和交付整理

目标：让项目可以被继续开发、评审和 push。

当前状态：

1. `README.md` 和 `README.zh-CN.md` 已补，包含本地安装、运行、验证、模型配置、Codex 子任务 runner 配置、截图路径、当前边界和下一步。
2. `docs/TODO.md` 已同步任务执行 v2 当前进展：opt-in Codex 子任务 runner、JSONL 进度广播、打断取消子进程、失联运行态任务失败式恢复、任务结果附件回流和后续 resume / artifact registry。
3. 当前任务分支已形成独立提交 `ac6afd9 feat: refine task chat flow`，可以 push 为 `codex/aurabot-task-system`。

## Push 准备

当前准备 push 当前分支 `codex/aurabot-task-system`。本分支包含：

1. 任务结果展示和文件附件回流。
2. 主聊天任务创建、查询、打断、修改、启动和重复查询的人话回复。
3. 任务 gate 放宽与外部 gate 本地护栏。
4. 任务状态条人话展示。
5. 相关 unit、integration 和 e2e 测试。

不纳入本次 push 的本地临时产物：

1. `artifacts/memory-debug-smoke-2026-05-05.png`
2. `artifacts/test-github-weekly-report-2026-05-05.md`
3. `artifacts/y_equals_x_squared.svg`
4. `docs/github-weekly-report-2026-05-05.md`
