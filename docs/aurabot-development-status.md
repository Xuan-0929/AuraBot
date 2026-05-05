# AuraBot 当前开发状态

更新时间：2026-05-05

这份文档记录当前 live2D/console 集成分支已经完成的工程事实、最近一次验证结果，以及下一步最应该推进的任务。它是执行状态板，不替代 PRD，也不替代 MVP 计划。

当前工作分支：`codex/aurabot-live2d-console-ui`

当前集成提交：`de65810 Merge branch 'codex/aurabot-persona-feel' into codex/aurabot-live2d-console-ui`

分支边界：这一侧已经集成 Live2D 桌宠体验、主控制台、任务卡控制、流式回复、模型 gate、记忆调试和 follow-up 主动提醒。后续如果继续并行开发，记忆模型/配置大改、任务执行器大改、桌宠 UI 大改仍建议分支隔离，避免在 `chatOrchestrator`、`createWindow`、`store`、IPC contract 和 renderer 样式上互相抢修改权。

## 当前产品形态

AuraBot 当前已经从“传统聊天界面”收敛为桌宠优先形态：

1. 默认启动一个透明、置顶的小型桌面 Live2D 小人窗口，平时只展示小人本体。
2. 鼠标悬浮到小人上时，小人下方浮现半透明 `Drag / Voice / Console` 控制，同时侧边出现一个小型 `Chat` 气泡。
3. 点击 `Chat` 小气泡后会展开成桌宠内联输入框，消息以 `pet_text` 来源进入主对话；展开层绝对定位，不会挤走 Live2D 模型。
4. 小人眼动不再只依赖窗口内 pointer event；主进程轮询系统全局鼠标位置并通过 IPC 传给 renderer，鼠标在窗口外时 Hiyori 也能朝指针方向看。
5. 用户点击控制台按钮后，打开或聚焦独立控制台视窗进行完整主线程对话。
6. 控制台输入 `Enter` 直接发送，`Ctrl+Enter` 换行；用户消息和输入框清空都先本地即时完成，gate/模型结果随后回填。
7. `chatModel` 已支持流式回复保存和广播，控制台能接收 `chat:message-updated` 逐步更新助手消息。
8. 任务创建、查询、打断、修改、启动都通过控制台里的主聊天线程完成；任务状态卡也提供取消和删除按钮作为快速控制。
9. 任务完成、桌宠语音回复和到期 follow-up 可以通过小人气泡临时浮现。
10. 记忆系统当前是“分层摘要 + 图状联想 + 可选 memoryModel 增强”的 MVP；已补到期 follow-up 调试评估与主动提醒通道。

## 已完成

| 领域 | 状态 | 说明 |
| --- | --- | --- |
| Electron 项目骨架 | 已完成 | `Electron + React + TypeScript + Vite + pnpm` 已可构建和运行。 |
| 双窗口桌宠壳层 | 已完成 | `createPetWindow()` 默认创建桌面小人，`openConsoleWindow()` 按需打开控制台。 |
| Live2D 真实模型 | 已完成 | 已接入官方样例 `Hiyori`，Cubism Core 本地打包，不依赖启动时 CDN。 |
| Live2D 桌宠交互层 | 部分完成 | 悬浮控制条、拖拽按钮、控制台按钮、点击穿透设置、任务完成气泡、位置持久化、状态 motion 映射、悬停小聊天气泡、展开式桌宠输入框和全局鼠标追踪眼动已接入。 |
| SQLite 数据层 | 已完成 | 已有 profiles、souls、sessions、messages、memory、tasks、event_logs 等表。 |
| 单主聊天线程 | 已完成 MVP | 主 session 持久化消息，控制台负责完整对话；用户消息乐观上屏，不再等待 gate/chat 模型返回。 |
| 模型适配层 | 部分完成 | 已有 `gateModel` / `chatModel` / `memoryModel` 注入接口、OpenAI-compatible provider、userData/系统级 JSON 配置、env 配置、deterministic fallback 和 chat streaming；还缺用户新消息打断、retry/backoff 和连接状态 UI。 |
| Gate/触发判断 | 已完成 MVP | 可判断碎片输入等待、完整输入回复、话题是否结束、聊天/任务/任务控制分类；强 gate 判定已延后到模型侧，外部模型异常时仍保留 fallback。 |
| 记忆 v1/v2 写入层 | 部分完成 | 支持 preference、event、follow_up 写入，摘要层和关系边召回；显式配置 `memoryModel` 后可用 API 做结构化记忆抽取、重复记忆合并和候选关系边判断；App 运行期间到期 follow_up 可通过后台 scheduler 推送桌宠气泡。 |
| 任务 v1 | 已完成 MVP | 可从主聊天创建任务、按聊天 hint 命中特定任务、查询状态、打断、修改、启动；默认 deterministic runner，显式配置后可调用本机 Codex 子任务 runner。任务完成后可把结果文件作为消息附件回流主聊天，并提供“打开文件 / 所在文件夹”操作；任务状态卡已补取消和删除按钮。 |
| 任务体验文案 | 已完成第一版 | 任务创建、查询、完成、失败、重复开始和状态条都已去掉内部状态词，改成伴侣兼助手式中文回复。 |
| 记忆调试视图 | 已完成第二版 | 控制台 `Memory Debug` 面板可查看当前 memory nodes、edges、summaries、最近 memory event，以及最近一次 recall 的 query、planner terms、seed、扩散节点和 traversed edges。 |
| 记忆评估语料 | 已完成第一版 | `corepack pnpm eval:memory` 可跑固定 recall fixture，覆盖直接偏好召回、事件到偏好图跳转、follow_up 排除不相关提醒。 |
| 事件日志 | 部分完成 | 已写入 gate、memory、task、chat 关键日志；已有 memory debug 读取面板，还缺更完整的日志开关和路径可视化。 |
| E2E 演示流 | 已完成 MVP | 桌宠启动、悬浮控制开控制台、桌宠设置同步、位置持久化、状态同步、记忆、碎片等待、任务控制路径都有 e2e 覆盖。 |

## 最近一次验证记录

最近一次完整验证在 2026-05-05 当前 live2D/console 集成分支完成：

| 命令/检查 | 结果 |
| --- | --- |
| `corepack pnpm vitest run tests/unit/renderer/desktopPet.spec.tsx tests/unit/main/petCursorTracking.spec.ts tests/unit/renderer/store.spec.ts` | 3 个 test files、11 个 tests 通过，确认悬停聊天气泡、全局鼠标追踪和 store 订阅仍在 |
| `corepack pnpm test:unit` | 29 个 test files、95 个 tests 通过 |
| `corepack pnpm typecheck` | 通过 |
| `corepack pnpm build` | 通过 |
| `corepack pnpm test:e2e` | 13 个 e2e tests 通过 |

可视化产物：

1. 桌面小人截图：`artifacts/desktop-pet.png`
2. 控制台截图：`artifacts/console-window.png`

## 当前边界

1. 模型角色默认仍走 deterministic fallback；外部模型调用需要本地 `aurabot.config.json` / `config.json`、macOS 系统级 `/Library/Application Support/AuraBot/config.json`，或环境变量。
2. 用户消息已经即时显示，模型回复已支持流式 message update；还没有用户新消息打断 in-flight generation。
3. 记忆写入层可由 `memoryModel` 接管抽取、合并和候选边判断；召回层也可由 `memoryModel` 生成语义检索词、目标类型和重排 seed node。到期 `follow_up` 可在下一次主聊天回复中触发并标记完成，也可在 App 运行期间由后台 scheduler 推送桌宠气泡；还没有系统级通知、离线调度或 embedding。
4. 任务 runner 可显式配置为本机 Codex 子任务执行器，能广播 JSONL 子任务进度，在主聊天打断时取消子进程，并在启动时把失联运行态任务标记为失败；完成后的文件结果已可作为主聊天附件回流并打开，还没有真正 resume 和结构化 artifact registry。
5. Live2D 状态行为当前复用 Hiyori 现有 motion group；全局鼠标追踪眼动已接入，但还没有定制表情资源或更细的动画状态机。
6. 语音输入当前走 Chromium 原生 `SpeechRecognition`，运行时不支持时只显示轻提示；还没有稳定 STT adapter。
7. 还没有 Profile 切换 UI、定时任务、桌面通知、打包发布、开机自启。

## 下一步推荐

### 1. Live2D 桌宠行为层

目标：让小人不只是挂在桌面，而是能表达 AuraBot 当前状态。

建议任务：

1. 继续细化状态动作：为 `idle / listening / thinking / replying / tasking` 选择更贴切的 motion 和后续 expression 资源。
2. 继续细化点击穿透：小人主体可穿透，悬浮控制区仍可稳定点击。
3. 已完成第一版：悬停时展示小型 `Chat` 气泡，点击展开成桌宠输入框，并把消息传给主对话。
4. 已完成第一版：主进程轮询系统全局鼠标位置，Live2D 在窗口外也能追踪鼠标眼动。
5. 接入稳定 STT adapter，替换当前浏览器原生语音识别 MVP。
6. 继续细化主动提醒：当前已复用气泡通道补后台 follow-up 触发入口，下一步是系统通知、提醒权限和更细的触发策略。
7. 增加开发期诊断面板，展示 Live2D `renderState`、motion、窗口 bounds、click-through 状态和全局 cursor 追踪状态。

### 2. 真实模型适配层

目标：把已接入的可配置模型调用打磨成可长期运行的模型层。

建议任务：

1. 已完成：流式回复已接入，助手消息可通过 `chat:message-updated` 增量更新。
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
6. 已完成第一步：App 运行期间 `MemoryFollowUpScheduler` 会后台扫描到期 `follow_up`，通过桌宠气泡主动提醒，并写入 `memory.follow_up_triggered` 日志。
7. 已完成第二版：控制台 `Memory Debug` 面板可查看 nodes、edges、summaries 计数、当前节点、关系边、最近 memory event，以及最近一次 recall 的 query、planner terms、seed、扩散节点和 traversed edges。
8. 已完成第一版：`corepack pnpm eval:memory` 可跑固定 recall fixture，比较直接关键词召回、图状联想召回和不相关 follow_up 排除。
9. 下一步：补系统通知和 App 未运行时的提醒边界提示。
10. 下一步：把模型 planner 原始响应和 fallback 原因纳入 debug event。
11. 下一步：继续扩展更真实的多轮记忆评估语料。

### 4. 任务执行 v2

目标：从任务状态占位进入真实可执行代理。

建议任务：

1. 已完成：opt-in Codex 子任务 runner 支持创建任务后后台把 task prompt 交给本机 `codex exec`，实时广播 JSONL 进度，查询时显示最近进展，打断时取消子进程，并把最终结果回流主聊天。
2. 已完成：任务完成消息可以带文件附件，主聊天中展示文件名并提供“打开文件 / 所在文件夹”，不直接暴露原始路径。
3. 已完成：任务状态条用人话展示 `待开始 / 处理中 / 完成 / 没跑成 / 已停下`，并清理 `Codex 子任务`、`queued`、`completed` 等内部词。
4. 已完成：任务 gate 覆盖直接产出型请求，`做一份读书笔记`、`写一份GitHub周报` 和 `帮我写一份实践论的读书笔记` 都会进入任务流；外部轻量 gate 误判时会被本地 `local-task-guard` 拉回任务。
5. 已完成：启动时会把上次遗留的 running/waiting 任务标记为失联失败，避免永久卡在运行态。
6. 已完成：任务状态卡提供取消和删除按钮，用于快速处理排队/运行/结束后的任务卡。
7. 下一步：设计 `TaskRegistry` 的真实任务类型、输入输出协议和结构化 artifact registry。
8. 下一步：设计真正 resume 策略，确保应用重启后能恢复或准确解释子任务状态。
9. 继续保持主聊天为主要控制面，任务卡按钮只作为快捷操作。

### 5. 文档和交付整理

目标：让项目可以被继续开发、评审和 push。

当前状态：

1. `README.md` 和 `README.zh-CN.md` 已补，包含本地安装、运行、验证、模型配置、Codex 子任务 runner 配置、截图路径、当前边界和下一步。
2. `docs/TODO.md` 已同步任务执行 v2 当前进展：opt-in Codex 子任务 runner、JSONL 进度广播、打断取消子进程、失联运行态任务失败式恢复、任务结果附件回流和后续 resume / artifact registry。
3. 当前 live2D/console 集成分支已合入 memory follow-up debug、任务卡控制、流式回复、模型 gate 延后、悬停聊天气泡和全局鼠标追踪眼动，可以 push 为 `codex/aurabot-live2d-console-ui`。

## Push 准备

当前准备 push 当前分支 `codex/aurabot-live2d-console-ui`。本分支包含：

1. 任务结果展示和文件附件回流。
2. 主聊天任务创建、查询、打断、修改、启动和重复查询的人话回复。
3. 任务状态卡取消/删除按钮。
4. 模型侧 gate 判定、chat streaming 和消息更新广播。
5. 桌宠悬停 `Chat` 小气泡、展开式输入框、`pet_text` 主聊天来源。
6. 主进程全局鼠标追踪和 Live2D 窗口外眼动。
7. memory follow-up debug/evaluation 和后台到期提醒桌宠气泡。
8. 相关 unit、integration 和 e2e 测试。

不纳入本次 push 的本地临时产物：

1. `artifacts/memory-debug-smoke-2026-05-05.png`
2. `artifacts/test-github-weekly-report-2026-05-05.md`
3. `artifacts/y_equals_x_squared.svg`
4. `docs/github-weekly-report-2026-05-05.md`
