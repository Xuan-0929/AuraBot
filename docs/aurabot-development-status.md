# AuraBot 当前开发状态

更新时间：2026-05-07

这份文档记录当前工作分支与近期已集成分支已经完成的工程事实、最近一次验证结果，以及下一步最应该推进的任务。它是执行状态板，不替代 PRD，也不替代 MVP 计划。

当前工作分支：`codex/aurabot-ui-productization-a1`

当前集成基线：`943dcd9 docs: record A1 UI productization checkpoint`

分支边界：这一侧已经集成 Live2D 桌宠体验、主控制台、任务便签快捷操作、流式回复、模型 gate、记忆调试、follow-up 主动提醒，以及 soul 驱动的统一回复/播报机制。后续如果继续并行开发，记忆模型/配置大改、任务执行器大改、桌宠 UI 大改仍建议分支隔离，避免在 `chatOrchestrator`、`createWindow`、`store`、IPC contract 和 renderer 样式上互相抢修改权。

## 2026-05-07 UI polish + notifications checkpoint

本次 checkpoint 继续承接 A1 产品化，不再是改大方向，而是把界面层级和提醒交付面做成真正可用的版本。

已完成：

1. 桌宠周围的悬浮控件、聊天浮层和控制台主界面继续收口到 A1 语言，减少工具后台感，保留更清晰的陪伴型入口层级。
2. 控制台正文已移除常驻 `Memory Debug`；深层调试能力不再占用日常主聊天空间。
3. 完整设置已新增独立 `记忆调试` 页，`Memory Debug` 现在通过完整设置承接，而不是散落在控制台首层。
4. 控制台右上角设置菜单继续保持“日常快捷设置”定位，只保留语言、点击穿透和完整设置入口。
5. App 运行中的系统通知 MVP 已接上 `follow_up / time_reminder / idle_nudge` 三类 trigger；桌宠气泡仍保留，系统通知作为第二条补充触达通道。
6. 点击系统通知后的默认回流行为已统一为：优先聚焦控制台；若控制台未打开，则打开并聚焦控制台。
7. 通知页已明确说明当前边界：系统通知只在 AuraBot 已运行时生效，尚未覆盖 App 未运行时的离线提醒。

本轮验证记录：

| 命令/检查 | 结果 |
| --- | --- |
| `corepack pnpm vitest run tests/unit/renderer/desktopPet.spec.tsx tests/unit/renderer/consoleWindow.spec.tsx tests/unit/renderer/messageList.spec.tsx tests/unit/renderer/settingsWindow.spec.tsx tests/unit/renderer/settingsCenterShell.spec.tsx tests/unit/renderer/memoryDebugPanel.spec.tsx tests/unit/renderer/notificationSettingsPage.spec.tsx tests/unit/main/systemNotificationService.spec.ts tests/unit/main/notificationSettingsStore.spec.ts tests/unit/core/proactive/proactiveTriggerEngine.spec.ts tests/unit/core/voice/voiceAdapter.spec.ts tests/unit/core/tasks/taskPresentation.spec.ts tests/integration/orchestration/chat-task.spec.ts` | 13 个 test files、67 个 tests 通过 |
| `corepack pnpm typecheck` | 通过 |
| `corepack pnpm build` | 通过 |
| `corepack pnpm test:e2e` | 14 个 e2e tests 通过 |

补充说明：

1. 在 `test:e2e` 之后，如需再跑 Node 侧 Vitest，仍可能需要执行一次 `corepack pnpm rebuild:node`，把 `better-sqlite3` 从 Electron ABI 切回 Node ABI。
2. 本阶段不包含 App 未运行时的离线通知、开机自启、复杂通知编排或系统通知 action button。

## 2026-05-07 A1 UI productization checkpoint

本次 checkpoint 把 A1 产品化界面收口到“陪伴优先”的稳定形态，并把验证口径同步到了当前真实 UI。`codex/aurabot-ui-productization-a1` 这一轮确认的不是新功能草图，而是已经能跑通的主舞台、控制台、设置中心和任务文案出口。

已完成：

1. 桌宠已经收敛成 A1 陪伴主舞台：默认启动仍然先进入 Live2D 桌面小人，聊天、任务提醒和状态变化都以桌宠为第一触点，而不是先打开传统应用壳。
2. 控制台已经切成“聊天室 + 右侧轻便签”结构；旧 task overlay 不再作为主路径，任务在 A1 控制台里以 sticky notes 形式贴在右侧，保留轻量的取消/删除动作。
3. Settings Center 已完成 A1 小屋式收口：设置页作为独立中心存在，承接 persona、pet behavior、Live2D、notification 和 model/provider 这些产品化入口，而不是把设置继续散落在控制台里。
4. 任务相关可见话术已经统一到 companion-first 口吻，覆盖 `task.status`、`task.interrupted`、`task.failed` 和完成后回流这些路径；用户看到的是 Aura 在陪着推进，而不是 runner/系统提示语。
5. `tests/e2e/app-launch.spec.ts` 已对齐当前 A1 UI：旧 `.task-status-card` / overlay 断言改成 console sticky notes 结构，任务状态文案断言改成当前真实 copy，并把长对话可达性断言切到当前控制台滚动行为。

本轮验证记录：

| 命令/检查 | 结果 |
| --- | --- |
| `corepack pnpm test:e2e tests/e2e/app-launch.spec.ts --grep "task|sticky|queued|cancel|remove|multiple|completion|long"` | 6 个 task/console 定向 e2e 通过 |
| `corepack pnpm vitest run tests/unit/renderer/desktopPet.spec.tsx tests/unit/renderer/taskStickyNotes.spec.tsx tests/unit/renderer/consoleWindow.spec.tsx tests/unit/renderer/settingsWindow.spec.tsx tests/unit/renderer/personaSettingsPage.spec.tsx tests/unit/renderer/notificationSettingsPage.spec.tsx tests/unit/core/tasks/taskPresentation.spec.ts tests/unit/core/voice/voiceAdapter.spec.ts tests/integration/orchestration/chat-task.spec.ts` | 9 个 test files、57 个 tests 通过 |
| `corepack pnpm typecheck` | 通过 |
| `corepack pnpm build` | 通过 |
| `corepack pnpm test:e2e` | 14 个 e2e tests 通过 |

补充说明：

1. 在先跑定向 e2e 后，Node 侧 `vitest` 首次执行遇到 `better-sqlite3` native module 被 `electron-rebuild` 切到 Electron ABI 的环境性报错；执行 `corepack pnpm rebuild:node` 后恢复，再按原验证命令重跑通过。
2. 本轮没有发现需要改应用代码的 blocker；剩余修复都收口在 `tests/e2e/app-launch.spec.ts` 的选择器、任务发送交互和滚动断言上。

## 2026-05-06 LangChain checkpoint

本次 checkpoint 把 LangChain 作为可选模型 provider 接入 AuraBot，但不替换现有产品主骨架。默认 `openai-compatible` 路径仍保留，`langchain-openai` 作为可配置增强路径用于验证后续 structured output、tools 和 LangGraph 的可行性。

已完成：

1. 新增 `@langchain/openai` 依赖。
2. 新增 `chatModel.provider = "langchain-openai"` 路径，通过 `LangChainOpenAIChatCompletionClient` 调用 `ChatOpenAI`，并保持现有 `ChatModel` 接口、流式 delta observer 和消息持久化行为。
3. 新增 `memoryModel.provider = "langchain-openai"` 路径，通过 `LangChainStructuredMemoryClient` 调用 `ChatOpenAI.withStructuredOutput(zodSchema)`，再复用现有 zod 校验、SQLite 记忆图和 merge/relation/recall planner。
4. 新增 `gateModel.provider = "langchain-openai"` 路径，通过 `LangChainStructuredGateClient` 调用 `ChatOpenAI.withStructuredOutput(structuredGateDecisionSchema)`，再复用现有 gate zod 校验和聊天/任务/任务控制归一化。
5. 设置窗口已新增三类模型角色的 provider 下拉选择，可在 OpenAI-compatible 和 LangChain OpenAI 之间切换；选择会写入 userData `config.json`，并在当前进程内重建对应 gate/chat/memory 服务。
6. README 中英文已补充 `langchain-openai` 配置示例和设置页 provider 选择说明，当前可分别对 `gateModel`、`chatModel` 和 `memoryModel` 开启 LangChain。
7. LangChain 分支 `codex/aurabot-langchain-chatmodel` 已 fast-forward 合入 `main`，当前远端 `main` 已包含 `80f3dc8 feat: add LangChain chat model adapter`、`36ab259 feat: add LangChain structured memory model` 和 `989fc03 docs: update LangChain development log`。
8. 设置页模型卡片现已补模型健康状态和 `Test connection`，主进程会区分 `未配置 / 已配置 / 测试中 / 已连接 / 连接失败 / 测试替身`，并写入 `model.health.*` 事件日志。

最近一次 LangChain gateModel 分支验证记录：

| 命令/检查 | 结果 |
| --- | --- |
| `corepack pnpm typecheck` | 通过 |
| `corepack pnpm vitest run tests/unit/core/llm/gateModel.spec.ts tests/unit/core/llm/langChainGateModel.spec.ts tests/unit/core/llm/modelBackedGateModel.spec.ts` | 3 个 test files、13 个 tests 通过 |
| `corepack pnpm vitest run tests/unit/renderer/settingsWindow.spec.tsx tests/unit/main/appConfigStore.spec.ts tests/unit/shared/settings.spec.ts` | 3 个 test files、7 个 tests 通过 |
| `corepack pnpm test:unit` | 42 个 test files、128 个 tests 通过 |
| `corepack pnpm build` | 通过 |
| `corepack pnpm test:e2e` | 14 个 e2e tests 通过 |
| `corepack pnpm rebuild:node` | 已在 e2e 后执行，恢复 Node 侧 `better-sqlite3` native module |

当前 LangChain 边界：

1. LangChain 现在是可选 provider，不是强制默认；用户端不再提供本地模型 fallback，没有 API Key 或 provider 未启用时会暴露模型配置/调用错误。
2. `memoryModel` 这条最适合继续加码，因为 LangChain structured output 对记忆抽取、合并、关系边和召回规划有直接价值。
3. 下一步若要“强制使用 LangChain”，建议先补模型连接状态、连接测试和失败可见提示，再决定是否把默认 provider 切到 LangChain。

## 2026-05-06 产品化 checkpoint

本次 checkpoint 把桌宠从开发启动形态推进到可双击打开的 macOS 应用形态，并补齐设置入口、模型配置可视化和中英文界面切换。

已完成：

1. 新增 `corepack pnpm package:mac`，通过 `scripts/package-macos.mjs` 生成 `/Users/xuan/Documents/AuraBot/dist/AuraBot.app`，双击后默认打开透明置顶的 Live2D 桌宠窗口，不再要求用户从终端输入启动命令。
2. 新增产品设置窗口，桌宠快捷控制区可直接打开设置；设置页包含通用设置、模型设置和人物设置。
3. 新增设置子菜单，原先散落在控制台里的语言、人物点击穿透和完整设置入口被集中到设置菜单里。
4. 新增 renderer i18n 层，`displayLanguage` 会驱动桌宠快捷控制、控制台、设置页、设置子菜单、聊天输入、消息附件、状态栏、记忆调试面板和任务状态卡等主要用户可见文本。
5. 新增安全的模型配置摘要 `PublicAppConfig`，renderer 只显示 provider、model、base URL、启用状态和 `hasApiKey`，不把 API Key 明文暴露给界面层。
6. 设置页模型区新增“打开 config 文件”入口，用 IPC 调用主进程打开 userData 下的 `config.json`，方便用户配置 API Key、Base URL 和模型名。
7. 已把本机当前 AuraBot config 复制到 userData 路径 `/Users/xuan/Library/Application Support/AuraBot/config.json`，解决 userData 空 config 覆盖系统级 config 后打包应用读不到 API 配置的问题。
8. 设置窗口实机验证中，中文界面下已能显示聊天、判断、记忆三组 `openai-compatible` 模型配置，并只显示“API Key 已保存”。

最近一次产品化验证记录：

| 命令/检查 | 结果 |
| --- | --- |
| `corepack pnpm typecheck` | 通过 |
| `corepack pnpm test:unit` | 36 个 test files、116 个 tests 通过 |
| `corepack pnpm build` | 通过 |
| `corepack pnpm test:e2e` | 14 个 e2e tests 通过 |
| `corepack pnpm package:mac` | 通过，生成 `dist/AuraBot.app` |
| 打包后手动打开 `dist/AuraBot.app` 并进入设置页 | 通过，Live2D 桌宠、中文设置页、模型配置摘要和 config 文件入口均可见 |

## 当前产品形态

AuraBot 当前已经从“传统聊天界面”收敛为桌宠优先形态：

1. 默认启动一个透明、置顶的小型桌面 Live2D 小人窗口，平时只展示小人本体。
2. 鼠标悬浮到小人上时，小人下方浮现半透明 `Drag / Voice / Console` 控制，同时侧边出现一个小型 `Chat` 气泡。
3. 点击 `Chat` 小气泡后会展开成桌宠内联输入框，消息以 `pet_text` 来源进入主对话；展开层绝对定位，不会挤走 Live2D 模型。
4. 小人眼动不再只依赖窗口内 pointer event；主进程轮询系统全局鼠标位置并通过 IPC 传给 renderer，鼠标在窗口外时 Hiyori 也能朝指针方向看。
5. 用户点击控制台按钮后，打开或聚焦独立控制台视窗进行完整主线程对话。
6. 控制台输入 `Enter` 直接发送，`Ctrl+Enter` 换行；用户消息和输入框清空都先本地即时完成，gate/模型结果随后回填。
7. `chatModel` 已支持流式回复保存和广播，控制台能接收 `chat:message-updated` 逐步更新助手消息。
8. 任务创建、查询、打断、修改、启动都通过控制台里的主聊天线程完成；右侧轻便签会保留当前任务与提醒，并提供取消和删除按钮作为快捷控制。
9. 任务完成、桌宠语音回复和到期 follow-up 可以通过小人气泡临时浮现。
10. 普通 bot 输出现在会同步进入桌宠头顶气泡，不再只在任务完成时弹出提示。
11. bot 输出的头顶气泡会按句子流式展示，控制台里的 assistant 回复也会按句拆成连续聊天气泡，视觉上更接近真实网络聊天。
12. 分句规则已抽成共享工具，当前识别 `。！？!?；;～~〜…` 等聊天句尾标点；英文句点暂不作为分隔符，避免拆坏文件名、URL 和小数。
13. 记忆系统当前是“分层摘要 + 图状联想 + 可选 memoryModel 增强”的 MVP；已补到期 follow-up 调试评估、主动提醒通道和完整设置中的独立记忆调试页。
14. 普通聊天、任务播报、任务完成/失败、任务控制、pet task notice 和 memory follow-up 已开始走统一 `Soul + ResponseWorkflow + VoiceAdapter` 机制，避免聊天像人、任务像系统通知的割裂体验。
15. 当前产品化分支已经可以打包为 `dist/AuraBot.app`，双击后直接进入桌面 Live2D 桌宠，不需要终端命令。
16. 设置能力已经从控制台内的小控件升级为独立设置窗口，语言、模型配置摘要、config 文件入口、人物点击穿透、通知和记忆调试都已集中到设置体系里。

## 已完成

| 领域 | 状态 | 说明 |
| --- | --- | --- |
| Electron 项目骨架 | 已完成 | `Electron + React + TypeScript + Vite + pnpm` 已可构建和运行。 |
| 双窗口桌宠壳层 | 已完成 | `createPetWindow()` 默认创建桌面小人，`openConsoleWindow()` 按需打开控制台。 |
| 产品设置窗口 | 已完成第二版 | `openSettingsWindow()` 可从桌宠快捷控制打开独立设置窗口，集中管理语言、模型配置摘要、模型 provider 选择、config 文件入口和人物点击穿透。 |
| macOS 应用打包 | 已完成第一版 | `corepack pnpm package:mac` 生成 `dist/AuraBot.app`，双击即可启动桌宠。 |
| 全局界面语言 | 已完成第一版 | `displayLanguage` 已覆盖桌宠、控制台、设置、设置菜单、聊天、任务状态和记忆调试等主要 UI 文本。 |
| Live2D 真实模型 | 已完成 | 已接入官方样例 `Hiyori`，Cubism Core 本地打包，不依赖启动时 CDN。 |
| Live2D 桌宠交互层 | 部分完成 | 悬浮控制条、拖拽按钮、控制台按钮、点击穿透设置、任务完成气泡、位置持久化、状态 motion 映射、悬停小聊天气泡、展开式桌宠输入框和全局鼠标追踪眼动已接入。 |
| 桌宠消息气泡 | 已完成第二版 | bot 普通输出、任务完成、语音回复、到期 follow-up 和到期时间提醒任务都会进入桌宠气泡通道；普通 bot 输出按句子流式排队展示，任务/提醒类气泡可优先打断过时普通聊天气泡。 |
| SQLite 数据层 | 已完成 | 已有 profiles、souls、sessions、messages、memory、tasks、event_logs 等表。 |
| 单主聊天线程 | 已完成 MVP | 主 session 持久化消息，控制台负责完整对话；用户消息乐观上屏，不再等待 gate/chat 模型返回。 |
| 控制台消息展示 | 已完成第二版 | assistant 回复在 UI 层按句子拆成多个连续气泡，底层消息仍保持一条完整记录；用户消息不拆，任务附件挂在最后一句气泡下。 |
| 模型适配层 | 部分完成 | 已有 `gateModel` / `chatModel` / `memoryModel` 注入接口、OpenAI-compatible provider、三类模型角色的 LangChain POC provider、userData/系统级 JSON 配置、env 配置、chat streaming、设置页安全摘要、provider 选择、模型健康状态和按角色连接测试；用户端已移除本地模型 fallback，deterministic 模型仅保留给显式测试开关；还缺用户新消息打断、retry/backoff 和更细的错误恢复。 |
| Gate/触发判断 | 已完成第二版 | 可判断碎片输入等待、完整输入回复、话题是否结束、聊天/任务/任务控制分类；任务内已细分 `result_output` 和 `time_reminder`，轻量 gateModel 需要给出任务子类型；外部模型异常时不再用本地 gate 兜底。 |
| 活人感统一回复机制 | 已完成第一版 | `Soul` 只保留人格、语气、原则和语义禁忌；`ResponseWorkflow` 描述场景、事实和安全边界；`VoiceAdapter` 结合二者生成最终用户可见文本。任务完成等场景可以接近“本人自然告诉你结果并邀请你看一眼”的语感，但不会把样例当固定模板。 |
| 主动消息触发 | 已完成第三版 | `ProactiveTriggerEngine` 统一支持到期 follow-up、到期 `time_reminder` 任务和空闲轻提醒；默认 10 秒扫描一次到期提醒；主动消息继续进入桌宠气泡和事件日志，同时在 App 运行中可走系统通知通道；只有用户回应后，后续真实对话才进入主聊天历史。空闲主动消息等待时间已进入设置页。 |
| 记忆 v1/v2 写入层 | 部分完成 | 支持 preference、event、follow_up 写入，摘要层和关系边召回；显式配置 `memoryModel` 后可用 API 做结构化记忆抽取、重复记忆合并和候选关系边判断；App 运行期间到期 follow_up 可通过后台 scheduler 推送桌宠气泡。 |
| 任务 v1 | 已完成第二版 | 任务已细分为结果产出型 `result_output` 和时间提醒型 `time_reminder`。结果产出任务可从主聊天创建、按聊天 hint 命中特定任务、查询状态、打断、修改、启动；默认 deterministic runner，显式配置后可调用本机 Codex 子任务 runner。时间提醒任务可由“两分钟后提醒我……”这类输入创建，记录 `dueAt`，到点由桌宠气泡提醒并标记完成。A1 控制台当前通过右侧轻便签展示任务与提醒，并保留取消/删除快捷操作。 |
| 任务体验文案 | 已完成第二版 | 任务创建、查询、完成、失败、重复开始和状态条都已去掉内部状态词，并接入 `VoiceAdapter`。任务事实由 workflow 提供，最终表达由 soul 约束为更自然的伴侣兼助手式回复。 |
| 记忆调试视图 | 已完成第三版 | `Memory Debug` 已从控制台正文迁移到完整设置中的独立页，可查看当前 memory nodes、edges、summaries、最近 memory event，以及最近一次 recall 的 query、planner terms、seed、扩散节点和 traversed edges。 |
| 记忆评估语料 | 已完成第一版 | `corepack pnpm eval:memory` 可跑固定 recall fixture，覆盖直接偏好召回、事件到偏好图跳转、follow_up 排除不相关提醒。 |
| 事件日志 | 部分完成 | 已写入 gate、memory、task、chat 关键日志；已有 memory debug 读取面板，还缺更完整的日志开关和路径可视化。 |
| E2E 演示流 | 已完成 MVP | 桌宠启动、悬浮控制开控制台、桌宠设置同步、位置持久化、状态同步、记忆、碎片等待、任务控制路径都有 e2e 覆盖。 |

## 最近一次验证记录

2026-05-06 在 `codex/aurabot-persona-feel` 分支完成用户端模型 fallback 移除、设置页 provider 收敛和测试替身显式化验证：

| 命令/检查 | 结果 |
| --- | --- |
| `corepack pnpm typecheck` | 通过 |
| `corepack pnpm vitest run tests/unit/core/llm/chatModel.spec.ts tests/unit/core/llm/modelBackedChatModel.spec.ts tests/unit/core/llm/gateModel.spec.ts tests/unit/core/llm/modelBackedGateModel.spec.ts tests/unit/core/voice/voiceAdapter.spec.ts tests/unit/core/memory/modelBackedMemoryModel.spec.ts tests/unit/main/appConfigStore.spec.ts tests/unit/renderer/settingsWindow.spec.tsx tests/unit/shared/settings.spec.ts tests/unit/core/bootstrap/loadConfig.spec.ts` | 10 个 test files、50 个 tests 通过，覆盖无 API 配置不生成用户端本地回复、模型失败不静默兜底、设置页不暴露 Local fallback |
| `corepack pnpm test` | 52 个 test files、186 个 tests 通过 |
| `corepack pnpm build` | 通过 |
| `corepack pnpm test:e2e` | 14 个 e2e tests 通过 |
| `git diff --check` | 通过 |

最近一次 live2D/console 分句气泡更新验证在 2026-05-05 当前 `codex/aurabot-live2d-console-ui` 分支完成：

| 命令/检查 | 结果 |
| --- | --- |
| `corepack pnpm vitest run tests/unit/shared/sentences.spec.ts tests/unit/main/petActivity.spec.ts tests/unit/renderer/messageList.spec.tsx` | 3 个 test files、9 个 tests 通过，覆盖共享分句规则、头顶气泡分句和控制台分句 |
| `corepack pnpm vitest run tests/unit/main/petActivity.spec.ts tests/unit/renderer/store.spec.ts` | 2 个 test files、11 个 tests 通过，覆盖桌宠气泡队列和任务完成气泡优先级 |
| `corepack pnpm typecheck` | 通过 |
| `corepack pnpm test:unit` | 32 个 test files、109 个 tests 通过 |
| `corepack pnpm test:e2e` | 13 个 e2e tests 通过 |
| `git diff --check` | 通过 |

最近一次完整验证在 2026-05-05 当前 `codex/aurabot-persona-feel` 分支完成：

| 命令/检查 | 结果 |
| --- | --- |
| `corepack pnpm typecheck` | 通过 |
| `corepack pnpm test` | 50 个 test files、165 个 tests 通过，覆盖 voice adapter、任务播报、proactive trigger、memory follow-up 和既有单元/集成路径 |
| `corepack pnpm build` | 通过 |
| `corepack pnpm test:e2e` | 13 个 e2e tests 通过 |
| `git diff --check` | 通过 |

上一次 live2D/console 集成分支验证记录：

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

1. 模型角色默认进入未配置状态，不再走用户端 deterministic fallback；外部模型调用需要本地 `aurabot.config.json` / `config.json`、macOS 系统级 `/Library/Application Support/AuraBot/config.json`，或环境变量。`gateModel`、`chatModel` 和 `memoryModel` 均可选 `provider: "langchain-openai"`。
2. 用户消息已经即时显示，模型回复已支持流式 message update；还没有用户新消息打断 in-flight generation。
3. 记忆写入层可由 `memoryModel` 接管抽取、合并和候选边判断；召回层也可由 `memoryModel` 生成语义检索词、目标类型和重排 seed node。到期 `follow_up` 可在下一次主聊天回复中触发并标记完成，也可在 App 运行期间同时走桌宠气泡与系统通知；还没有 App 未运行时的离线调度或 embedding。
4. 任务 runner 可显式配置为本机 Codex 子任务执行器，能广播 JSONL 子任务进度，在主聊天打断时取消子进程，并在启动时把失联运行态任务标记为失败；完成后的文件结果已可作为主聊天附件回流并打开，还没有真正 resume 和结构化 artifact registry。
5. `Soul + ResponseWorkflow + VoiceAdapter` 已覆盖任务和提醒类文本出口的第一批场景；普通聊天仍由 `chatModel` 直接生成，但已经读取同一套 soul 字段。下一步要继续补错误提示和设置页提示等剩余用户可见文本。
6. Live2D 状态行为当前复用 Hiyori 现有 motion group；全局鼠标追踪眼动已接入，但还没有定制表情资源或更细的动画状态机。
7. 语音输入当前走 Chromium 原生 `SpeechRecognition`，运行时不支持时只显示轻提示；还没有稳定 STT adapter。
8. 还没有 Profile 切换 UI、App 未运行时的离线提醒、开机自启和复杂通知编排。

## 下一步推荐

### 1. Live2D 桌宠行为层

目标：让小人不只是挂在桌面，而是能表达 AuraBot 当前状态。

建议任务：

1. 继续细化状态动作：为 `idle / listening / thinking / typing / tasking` 选择更贴切的 motion 和后续 expression 资源。
2. 继续细化点击穿透：小人主体可穿透，悬浮控制区仍可稳定点击。
3. 已完成第一版：悬停时展示小型 `Chat` 气泡，点击展开成桌宠输入框，并把消息传给主对话。
4. 已完成第一版：主进程轮询系统全局鼠标位置，Live2D 在窗口外也能追踪鼠标眼动。
5. 已完成第二版：普通 bot 输出会在头顶气泡按句子流式出现，连续句子进入队列；任务完成、提醒等更高优先级气泡可打断旧的普通聊天气泡。
6. 接入稳定 STT adapter，替换当前浏览器原生语音识别 MVP。
7. 继续细化主动提醒：当前已复用气泡通道补后台 follow-up 触发入口，下一步是系统通知、提醒权限和更细的触发策略。
8. 增加开发期诊断面板，展示 Live2D `renderState`、motion、窗口 bounds、click-through 状态和全局 cursor 追踪状态。

### 2. 真实模型适配层

目标：把已接入的可配置模型调用打磨成可长期运行的模型层。

建议任务：

1. 已完成：流式回复已接入，助手消息可通过 `chat:message-updated` 增量更新。
2. 已完成：普通 assistant 回复在控制台按句子拆成连续气泡，但底层仍保持完整 message，避免影响记忆、任务附件和历史记录。
3. 支持用户新消息打断当前强模型生成。
4. 为 provider 调用增加重试和更细的失败恢复策略。
5. 已完成第一版：设置窗口可显示当前模型连接状态并手动测试连接；下一步是把这些失败提示继续接到主聊天和更细的用户恢复路径。
6. 为 gate 模型输出增加线上样本日志，后续评估“是否抢话/是否漏接/任务误判”。

### 2.5 活人感统一回复机制

目标：让所有用户可见文本都像同一个 Aura 在说话，而不是不同模块各写各的提示语。

已完成：

1. 已新增设计文档 `docs/soul-response-workflow.md`，明确 `Soul` 只描述人格、语气、原则和语义禁忌，不承载具体任务模板。
2. 已扩展 `Soul` schema 和默认 soul，增加 `tone / principles / taboos`，并补 SQLite 迁移和兼容读取。
3. 已新增 `VoiceAdapter`，用 `soul + workflow + facts` 生成最终文本；配置 chat model 时走模型改写，失败或未配置时直接暴露模型错误，不再生成用户端 deterministic 文案。
4. 已覆盖第一批场景：`task.accepted`、`task.status`、`task.completed`、`task.failed`、`task.interrupted`、`task.control_not_found`、`memory.follow_up`、`pet.task_activity`。
5. 已补测试，覆盖附件事实安全、模型 prompt 构造、无用户端 fallback 约束和任务/e2e 语义断言。
6. 已新增 `proactive.idle_nudge` workflow scene，并由 `ProactiveTriggerEngine` 驱动桌宠主动轻提醒。

下一步：

1. 把模型连接失败、IPC 错误提示、设置页轻提示等剩余用户可见文本继续接入 workflow。
2. 给 model-backed voice adapter 增加语义级质量检查：不能虚构附件、不能暴露内部 runner、不能变成客服/系统通知口吻。
3. 记录 voice adapter 的 scene、model 路径和失败原因，方便后续调试活人感劣化。

### 2.6 主动消息触发

目标：让 Aura 能主动冒出来一下，但不把没有回应的一句话污染成正式聊天历史。

已完成：

1. 已新增 `ProactiveTriggerEngine`，统一管理主动触发源，而不是把主动逻辑散在 `chatOrchestrator` 或单一 follow-up scheduler 里。
2. 第一批 trigger 已接入：到期 `follow_up`、到期 `time_reminder` 任务、空闲轻提醒 `idle_nudge`。
3. 主动触发当前只发 `PetActivity`，不会直接创建 `assistant message` 或写入 main session。
4. 用户发送下一条真实输入时，会走现有 `sendChatMessage` 路径进入主聊天，因此只有“被接住的主动触发”才会形成正式对话历史。
5. 已补事件日志：`proactive.trigger_evaluated`、`proactive.message_emitted`、`proactive.suppressed`、`proactive.user_activity_recorded`、`proactive.scheduler_failed`。
6. 已把空闲主动消息等待时间纳入 `proactive.idleThresholdMs` 配置，并在设置页提供分钟级修改入口。

下一步：

1. 继续扩展触发源，例如 memory recall nudge、deadline nudge。
2. 增加更细的打扰判断：最近是否刚收到桌宠气泡、是否正在 typing/tasking、是否刚被用户明确打断。
3. 给主动消息增加用户级开关、更细 cooldown 配置和 debug 面板查看入口。

### 3. 记忆 v2

目标：让 memory 更像联想，而不只是规则命中。

建议任务：

1. 已完成第一步：显式配置 `memoryModel` 后，可用 LLM 替换记忆抽取；用户端模型失败不再回退到 deterministic 记忆逻辑。
2. 已完成第一步：写入队列可通过 merge planner 更新重复记忆，而不是只按完全相同 content 去重。
3. 已完成第一步：候选关系边可通过 relation planner 由 API 判断，并限制 target node 必须来自已有节点。
4. 已完成第一步：召回阶段可通过 recall planner 获取 query terms、target types 和 ranked node ids，减少只靠关键词命中。
5. 已完成第一步：到期 `follow_up` 会在下一次主聊天回复中提醒，并在触发后标记为 `completed`。
6. 已完成第一步：App 运行期间 `MemoryFollowUpScheduler` 会后台扫描到期 `follow_up`，通过桌宠气泡主动提醒，并写入 `memory.follow_up_triggered` 日志。
7. 已完成第二版：控制台 `Memory Debug` 面板可查看 nodes、edges、summaries 计数、当前节点、关系边、最近 memory event，以及最近一次 recall 的 query、planner terms、seed、扩散节点和 traversed edges。
8. 已完成第一版：`corepack pnpm eval:memory` 可跑固定 recall fixture，比较直接关键词召回、图状联想召回和不相关 follow_up 排除。
9. 下一步：补系统通知和 App 未运行时的提醒边界提示。
10. 下一步：把模型 planner 原始响应和失败原因纳入 debug event。
11. 下一步：继续扩展更真实的多轮记忆评估语料。

### 4. 任务执行 v2

目标：从任务状态占位进入真实可执行代理。

建议任务：

1. 已完成：opt-in Codex 子任务 runner 支持创建任务后后台把 task prompt 交给本机 `codex exec`，实时广播 JSONL 进度，查询时显示最近进展，打断时取消子进程，并把最终结果回流主聊天。
2. 已完成：任务完成消息可以带文件附件，主聊天中展示文件名并提供“打开文件 / 所在文件夹”，不直接暴露原始路径。
3. 已完成：任务状态条用人话展示 `待开始 / 处理中 / 完成 / 没跑成 / 已停下`，并清理 `Codex 子任务`、`queued`、`completed` 等内部词。
4. 已完成：任务 gate 覆盖直接产出型请求，`做一份读书笔记`、`写一份GitHub周报` 和 `帮我写一份实践论的读书笔记` 都会进入任务流；外部轻量 gate 误判时会被本地 `local-task-guard` 拉回任务。
5. 已完成：启动时会把上次遗留的 running/waiting 任务标记为失联失败，避免永久卡在运行态。
6. 已完成：右侧任务便签提供取消和删除按钮，用于快速处理排队/运行/结束后的任务。
7. 下一步：设计 `TaskRegistry` 的真实任务类型、输入输出协议和结构化 artifact registry。
8. 下一步：设计真正 resume 策略，确保应用重启后能恢复或准确解释子任务状态。
9. 继续保持主聊天为主要控制面，任务便签按钮只作为快捷操作。

### 5. 文档和交付整理

目标：让项目可以被继续开发、评审和 push。

历史状态：

1. `README.md` 和 `README.zh-CN.md` 已补，包含本地安装、运行、验证、模型配置、Codex 子任务 runner 配置、截图路径、当前边界和下一步。
2. `docs/TODO.md` 已同步任务执行 v2 当前进展：opt-in Codex 子任务 runner、JSONL 进度广播、打断取消子进程、失联运行态任务失败式恢复、任务结果附件回流和后续 resume / artifact registry。
3. 当时 live2D/console 集成分支已合入 memory follow-up debug、任务便签快捷操作、流式回复、模型 gate 延后、悬停聊天气泡和全局鼠标追踪眼动，可以 push 为 `codex/aurabot-live2d-console-ui`。
4. 当时 persona-feel 分支已补 soul response workflow 文档、voice adapter 第一版和相关测试，等待继续验证实际模型输出的活人感。

## 历史 Push 准备记录

以下为当时准备 push `codex/aurabot-live2d-console-ui` 时的交付清单：

1. 任务结果展示和文件附件回流。
2. 主聊天任务创建、查询、打断、修改、启动和重复查询的人话回复。
3. 右侧任务便签取消/删除按钮。
4. 模型侧 gate 判定、chat streaming 和消息更新广播。
5. 桌宠悬停 `Chat` 小气泡、展开式输入框、`pet_text` 主聊天来源。
6. 主进程全局鼠标追踪和 Live2D 窗口外眼动。
7. memory follow-up debug/evaluation 和后台到期提醒桌宠气泡。
8. 普通 bot 输出头顶气泡、按句流式队列、控制台分句气泡和聊天式标点分句规则。
9. 相关 unit、integration 和 e2e 测试。

不纳入本次 push 的本地临时产物：

1. `artifacts/memory-debug-smoke-2026-05-05.png`
2. `artifacts/test-github-weekly-report-2026-05-05.md`
3. `artifacts/y_equals_x_squared.svg`
4. `docs/github-weekly-report-2026-05-05.md`
