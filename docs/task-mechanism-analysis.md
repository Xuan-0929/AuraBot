# AuraBot 任务机制现状与优化策略

更新时间：2026-05-16

## 说明

这份文档面向中文团队协作，正文尽量使用中文表达。源码里的类型名、函数名、枚举值、文件路径和通信频道名会保留原文，例如 `TaskService`、`queued`、`task:changed`，方便回到代码中定位。

本文基于当前仓库实现梳理 AuraBot 的任务机制，并分析可改进点与后续优化策略。这里的“任务”不是单一队列表，而是由聊天入口、任务持久化、命令行子进程执行、主动提醒、主进程通信，以及桌宠/控制台展示共同组成的一套轻量任务系统。

## 1. 当前结论

当前任务机制已经形成可用的最小闭环：

1. 用户在主聊天线程里提出任务，门控判断模型将输入分类为 `task` 或 `task_control`。
2. `result_output` 任务会创建独立任务会话，并记录到 `tasks` 表。
3. 如果命令行任务执行器已启用，主进程会在后台启动 Codex、Claude Code 或 Hermes 子任务；否则使用确定性执行器作为开发和测试路径。
4. 子任务进度通过 `task_events` 追加记录，并通过主进程通信推送到渲染进程。
5. 控制台和桌宠以任务便签展示当前任务；控制台还提供取消、删除等快捷操作。
6. 完成、失败或被打断后，系统会把结果用 Aura 的口吻回流到主聊天，并在有文件产物时优先从 `task_artifacts` 生成附件。
7. `time_reminder` 任务复用任务表，记录 `dueAt`，由主动触发引擎轮询到期后提醒并标记完成。
8. 控制台任务便签已支持查看详情，可以看到状态、执行器、工作目录、安全模式、关键事件时间线、产物和最近错误。

整体定位更接近“聊天驱动的轻量任务协调器”，还不是完整的后台作业调度器、任务规划器或多代理编排系统。

## 2. 核心数据模型

任务数据结构定义在 `src/shared/schemas/task.ts`：

| 字段 | 作用 |
| --- | --- |
| `id` | 任务唯一标识，持久化时以 `task_` 前缀生成 |
| `profileId` | 归属的用户档案 |
| `parentSessionId` | 来源主聊天会话 |
| `sessionId` | 任务独立会话 |
| `kind` | 任务类型，目前为 `result_output` 或 `time_reminder` |
| `title` | 用户可见任务标题，也是子执行进程提示词的核心输入 |
| `status` | 任务状态：`queued`、`running`、`waiting`、`completed`、`failed`、`interrupted` |
| `latestProgress` | 当前最新进展摘要，供界面和状态回复使用 |
| `dueAt` | 提醒任务的到期时间 |
| `createdAt` / `updatedAt` | 创建与更新时间 |

任务事件 `TaskEvent` 是过程日志：

| 字段 | 作用 |
| --- | --- |
| `taskId` | 关联任务 |
| `eventType` | 事件类型，例如 `task.running`、`task.agent.progress`、`task.completed` |
| `summary` | 人类可读摘要 |
| `payload` | 结构化补充信息，例如执行器、工作目录、输出文件、标准输出、标准错误、文件改动 |

SQLite 表由 `src/core/storage/migrations.ts` 创建。当前已启用 `task_artifacts` 表的第一版文件产物记录；结果附件和打开文件校验优先使用产物表，`task_events.payload` 推断只作为兼容兜底。

## 3. 端到端机制

### 3.1 任务识别入口

聊天入口在 `src/core/orchestration/chatOrchestrator.ts`：

1. `gateModel` 返回 `inputKind`。
2. `inputKind=task` 时进入任务创建路径。
3. `taskKind=result_output` 时创建结果产出型任务。
4. `taskKind=time_reminder` 时先由 `src/core/tasks/reminderParser.ts` 解析相对时间，再创建提醒任务。
5. `inputKind=task_control` 时进入任务控制路径，支持查询、启动、打断和修改。

本地规则兜底主要在 `src/core/orchestration/gateEvaluator.ts` 与 `src/core/tasks/taskControlRouter.ts`。任务控制依赖中文关键词正则和标题匹配，例如“开始做”“进度”“停掉”“改成”。

### 3.2 创建与持久化

`TaskService.createTaskFromChat()` 是创建入口：

1. 通过 `SessionRepository.createTaskSession()` 创建任务会话。
2. 通过 `TaskRepository.create()` 插入任务，初始状态为 `queued`。
3. 写入 `task.created` 事件日志。
4. 返回 `Task` 给上层。

这个设计让主聊天会话和任务会话在数据层保持父子关系，便于以后扩展任务内消息、结果回放或子任务上下文。

### 3.3 自动执行路径

自动执行不在 `ChatOrchestrator` 内部触发，而是在主进程通信层完成：

1. 渲染进程调用 `chat:send-message`。
2. `registerIpc()` 等待 `chatOrchestrator.sendMessage()` 返回。
3. 如果返回 `createdTask.kind=result_output`，并且 `shouldAutoRunTasks(config.taskRunner)` 为真，就调用 `TaskService.runTaskInBackground()`。
4. 后台启动先通过 `TaskRepository.transitionStatus()` 原子抢占 `queued -> running`，抢占失败不会启动执行器。
5. 自动启动和手动启动都进入 `TaskExecutionCoordinator`。
6. 进度通过 `onProgress` 广播 `task:changed`。
7. 任务进入终态后，协调器收集文件产物、广播最终任务、创建聊天消息、推送桌宠活动，并将全局代理状态置回 `idle`。

`shouldAutoRunTasks()` 的条件是：任一命令行执行器启用，或历史配置 `taskRunner.provider === "codex"`。

### 3.4 执行器

当前有两类执行器：

1. `DeterministicTaskRunner`：开发和测试用，顺序写入开始、进展、完成。
2. `MultiCliTaskRunner`：真实子任务执行器，按优先级选择 Codex、Claude Code 或 Hermes。

`MultiCliTaskRunner` 的主要行为：

1. 过滤启用的执行器，并按 `priority` 升序选择。
2. 检查命令是否可用。
3. Codex 执行器额外校验工作目录是否位于 Git 仓库中。
4. 将任务状态更新为 `running`。
5. 通过 `child_process.spawn()` 启动子进程。
6. 把提示词写入标准输入。
7. Codex 路径使用 `--json` 和 `--output-last-message`，从 JSONL 事件中提取进度。
8. 正常退出后写 `task.completed`，失败时写 `task.failed`。
9. 支持 `AbortController`，取消时向子进程发送 `SIGTERM`。

子执行进程提示词由 `buildCliTaskPrompt()` 生成，包含任务标题、任务标识、中文回复要求，以及“如果创建或修改文件，需要列出路径”的要求。

### 3.5 任务控制

任务控制主要分为聊天控制和控制台快捷控制。

聊天控制：

| 动作 | 入口 | 当前行为 |
| --- | --- | --- |
| 查询 | `task_control` + `query` | 调用 `buildTaskStatusResponseWithVoice()` |
| 启动 | `task_control` + `start` | 如果任务是 `queued`，触发后台启动并立即返回处理中状态 |
| 打断 | `task_control` + `interrupt` | 更新为 `interrupted`，并调用执行器取消方法 |
| 修改 | `task_control` + `revise` | 更新标题，状态重置为 `queued` |

控制台快捷控制：

| 动作 | 通信频道 | 当前行为 |
| --- | --- | --- |
| 取消 | `task:cancel` | 仅允许活跃状态，更新为 `interrupted` |
| 删除 | `task:delete` | 仅允许终态任务，从任务列表删除 |
| 打开结果文件 | `task:open-result-file` | 仅允许打开任务事件中记录过的文件路径 |
| 在文件夹中显示 | `task:show-result-file-in-folder` | 同样做事件路径校验 |
| 查看详情 | `task:get-detail` | 返回状态、执行器、安全模式、时间线、产物和最近错误 |

### 3.6 提醒任务

提醒任务复用 `tasks` 表：

1. 用户说“两分钟后提醒我喝水”。
2. 门控判断识别为 `taskKind=time_reminder`。
3. `parseReminderRequest()` 解析相对时间，写入 `dueAt`。
4. 任务保持 `queued`，不会进入命令行执行器。
5. `ProactiveTriggerEngine` 每轮调用 `listDueReminderTasks()`。
6. 到期后生成桌宠活动和系统通知，调用 `completeReminderTask()` 标记 `completed`。
7. 提醒不会主动写入聊天历史。

这条路径和 `memory.follow_up`、`idle_nudge` 一起属于主动触发系统。

### 3.7 界面展示与用户反馈

渲染进程状态管理在初始状态中加载所有任务，并订阅：

1. `task:changed`：更新或插入任务。
2. `task:deleted`：从列表移除任务。

展示策略：

1. 桌宠只展示最多两个活跃任务：`queued`、`running`、`waiting`。
2. 控制台展示活跃任务和终态任务。
3. 活跃任务在控制台可取消。
4. 终态任务在控制台可删除。
5. 任务完成后，`taskPresentation` 会优先基于产物表生成附件，事件推断只作为兼容兜底。
6. 控制台任务便签提供“详情”入口，详情面板展示任务状态、执行上下文、耗时、产物、最近错误和事件时间线。

任务文案已接入 `VoiceAdapter`，目标是让状态、完成、失败、提醒都保持 Aura 的统一语气，而不是暴露执行器术语。

## 4. 当前机制的优势

### 4.1 聊天优先，控制自然

任务不是从独立后台面板发起，而是从主聊天流进入；查询、修改、打断也可以用自然语言完成。这符合 AuraBot “桌宠陪伴 + 主聊天控制面”的产品方向。

### 4.2 数据模型克制

当前任务表足够小，事件表承载过程信息，适合快速迭代。任务会话与主聊天会话的父子关系也为未来扩展留下空间。

### 4.3 命令行执行器可插拔

Codex、Claude Code、Hermes 通过统一 `TaskRunner` 接口接入；优先级、启用状态和超时可配置。Codex JSONL 进度已能转成细粒度任务进展。

### 4.4 用户可见反馈闭环完整

任务创建、进度、完成、失败、取消、删除、结果文件打开都有路径。桌宠、控制台、聊天消息、系统通知已经形成基本闭环。

### 4.5 已有测试覆盖关键路径

测试覆盖了任务创建、运行、后台进度、取消、失败、失联任务恢复、命令行执行器回退、Codex JSONL 进度、任务展示、提醒到期、控制路由等关键行为。

## 5. 可改进点

### 5.1 状态机已显式化，但迁移还没完全收尾

当前已经新增 `src/core/tasks/taskState.ts`，集中定义活跃、终态、可运行、可取消、可删除和合法状态转移。`TaskRepository.transitionStatus()` 也会拒绝非法转移，后台启动已经用它抢占 `queued -> running`。

但迁移期仍有一些边界需要继续收尾：

具体表现：

1. `waiting` 是活跃状态，但核心执行器路径尚未产生它。
2. `reviseTaskFromChat()` 会把选中的任务重置为 `queued`，后续应明确区分“修改未开始任务”和“重试/重新运行终态任务”。
3. `updateStatus()` 仍保留给迁移期和非抢占路径，后续应逐步收窄调用范围。
4. 提醒任务和结果产出任务复用同一状态枚举，文档和 UI 需要继续避免语义混淆。

风险是：随着执行器、规划器、重试和多任务并发增加，状态会变得难以推理。

### 5.2 执行协调器已收口，完整队列还没引入

当前已经新增 `TaskExecutionCoordinator`，自动启动和聊天手动启动都走同一个后台入口；进度广播、完成回流消息、桌宠活动和 `idle` 恢复也已经集中到协调器。

仍未解决的是完整后台队列能力：

1. 从测试或其他服务调用 `createTaskFromChat()` 仍只会创建 `queued`，是否自动执行依然由更上层决定。
2. 当前只保证同一个任务不会重复启动，没有全局并发上限。
3. 多任务同时运行时，全局代理状态仍只有 `tasking` 和 `idle`，无法表达“还有几个任务在跑”。

### 5.3 缺少任务并发与队列策略

目前没有显式的按用户档案队列、全局并发上限、同任务运行锁或执行器资源池。`runTaskInBackground()` 依赖读取到的当前状态判断是否可运行，但状态切换不是数据库原子抢占。

潜在问题：

1. 多个长任务同时运行时，全局代理状态只有 `tasking` 和 `idle`，无法准确表达多个任务状态。
2. 执行器优先级只决定选哪个命令行工具，不负责排队、限流或负载。
3. 如果未来允许用户批量创建任务，还需要显式排队、暂停和重试策略。

### 5.4 任务结果与产物管理不够结构化

当前已经启用 `TaskArtifactRepository`，第一版会把文件产物写入 `task_artifacts`；完成消息附件和打开文件校验优先读取产物表。

剩余问题：

1. 产物第一版只处理文件，还不处理 URL、长文本结果和日志产物。
2. 产物收集主要来自文件改动事件，最终回复中的明确路径提取还可以继续增强。
3. 标准输出和标准错误仍保存在事件载荷里，详情 UI 只展示摘要，但数据库体积治理还没做。
4. 还没有区分“最终可交付文件”“中间日志”“调试输出”。

### 5.5 任务事件查询能力有限

`task_events` 表只有 `payload_json`，`summary` 被塞进载荷内部。读取时可以恢复摘要，但数据库层不能直接按摘要、执行器、产物或严重程度查询。

这对调试和未来界面不利。例如控制台无法轻易展示“关键步骤时间线”“错误原因分类”“产物清单”。

### 5.6 任务控制理解偏规则化

任务控制目前依赖固定中文正则和简单字符重叠打分。它已经足够覆盖常见表达，但复杂多任务场景容易误选。

典型问题：

1. 多个标题相似任务时，字符重叠可能不够。
2. 用户说“把刚才那个改一下”时，缺少上下文指代解析。
3. 控制动作只覆盖查询、启动、打断、修改，缺少继续、重试、复制、归档、查看产物等动作。

### 5.7 提醒任务仍是应用运行中机制

`ProactiveTriggerEngine` 在应用启动后轮询到期任务。它可以处理应用运行期间的到期提醒，也会在下次轮询时发现已经过期但仍排队的提醒；但它不具备系统级离线调度能力。

边界：

1. 应用未运行时没有原生系统闹钟。
2. 没有开机后“补发错过提醒”的特殊体验。
3. `reminderParser` 当前主要支持相对时间，不支持完整自然日期、重复提醒和时区确认。

### 5.8 子任务可观测性已有第一版，还可以更强

当前已新增任务详情面板，能展示状态、执行器、工作目录、安全模式、关键事件时间线、产物清单和最近错误。任务事件也开始携带 `startedAt`、`completedAt` 和 `durationMs`。

后续还缺少：

1. 更完整的执行器选择决策视图。
2. 每个阶段的分段耗时统计。
3. 取消后子进程是否真正退出、是否超时强杀。
4. 模型或命令行错误的分类与用户可读解释。
5. 完整 stdout/stderr 的开发者调试入口和保留策略。

### 5.9 安全边界需要继续产品化

已有 Codex 工作目录 Git 仓库校验、安全沙盒和权限策略配置，但界面只暴露启用、优先级、超时。命令路径、工作目录、安全沙盒、权限策略仍主要依赖配置文件或环境变量。

后续如果让普通用户配置任务命令行执行器，需要把安全边界显式化：

1. 当前执行器会在哪个目录运行。
2. 能否写文件。
3. 是否允许请求权限。
4. 结果文件为什么可以打开。
5. 哪些路径被拒绝。

## 6. 优化策略

### 6.1 抽出显式任务状态机

建议新增一个小型状态机模块，例如 `src/core/tasks/taskStateMachine.ts`：

1. 定义合法转移表。
2. 定义活跃、终态、可运行、可取消、可删除等派生判断。
3. 所有状态更新先过状态机。
4. `TaskRepository` 增加条件更新接口，例如 `transitionStatus({ taskId, from, to })`。

建议初始转移：

| 起始状态 | 目标状态 |
| --- | --- |
| `queued` | `running`、`interrupted` |
| `running` | `waiting`、`completed`、`failed`、`interrupted` |
| `waiting` | `running`、`completed`、`failed`、`interrupted` |
| `failed` | `queued` |
| `interrupted` | `queued` |
| `completed` | 默认不允许转回，除非显式“重新运行”或“复制任务” |

这样可以把“修改任务”和“重试任务”从隐式重置变成明确动作。

### 6.2 把任务执行编排收敛到统一协调器

建议新增 `TaskExecutionCoordinator`，负责：

1. 创建后是否自动执行。
2. 启动任务时统一后台化。
3. 进度和终态回调。
4. 终态后生成聊天消息、桌宠活动和广播。
5. 防重复启动和并发控制。

这样 `registerIpc` 不需要知道太多任务执行细节，其他入口也可以复用同一套行为。

### 6.3 增加任务队列和并发限制

短期策略：

1. 每个用户档案默认最多 1 个运行中的 `result_output` 任务。
2. 全局执行器并发上限可配置，默认 1 或 2。
3. 排队任务按 `createdAt` 或用户手动优先级排序。
4. 启动任务时用数据库条件更新抢占运行权。

中期策略：

1. 引入 `queuedAt`、`startedAt`、`finishedAt`。
2. 支持任务优先级。
3. 支持暂停队列、恢复队列。
4. 界面展示“排队中，第 N 个”。

### 6.4 正式启用任务产物表

建议把产物从事件载荷中提升为结构化实体：

```ts
interface TaskArtifact {
  id: string;
  taskId: string;
  kind: "file" | "url" | "text" | "log";
  uri: string;
  name?: string;
  mimeType?: string;
  sizeBytes?: number;
  sourceEventId?: string;
  createdAt: string;
}
```

执行器完成后统一调用产物收集器：

1. 从 Codex 文件改动事件提取文件。
2. 从最终回复中提取明确声明的路径。
3. 保留 `output-last-message` 作为调试产物，而不是默认用户附件。
4. 对产物做路径白名单校验。
5. 界面只展示标记为可交付的文件、链接或文本产物。

### 6.5 改进任务控制路由

短期增强：

1. 增加“继续”“重试”“重新跑”“打开结果”“删掉已完成任务”等控制动作。
2. 选择任务时结合最近一次被提及任务、更新时间、状态和标题相似度。
3. 当匹配分数接近时，让 Aura 反问，而不是直接选一个。

中期增强：

1. 让 `gateModel` 输出结构化任务控制信息：`action`、`targetTaskHint`、`newTitle`、`confidence`。
2. 规则解析作为兜底。
3. 记录任务控制解析过程，便于调试误选。

### 6.6 让启动动作也走后台路径

聊天中“开始这个任务”不应长时间阻塞 `sendMessage`。建议：

1. `handleTaskControl(start)` 调用后台启动接口。
2. 立即回复“我开始弄了”并广播 `running` 状态。
3. 完成后通过现有终态消息回流。

这能让手动启动和自动启动拥有一致体验。

### 6.7 强化取消语义

当前取消向子进程发送 `SIGTERM`。建议补齐：

1. 记录请求取消的时间。
2. 若超时未退出，再升级为 `SIGKILL`。
3. 区分 `interrupted`、`cancel_requested`、`cancel_failed` 等内部事件。
4. 界面显示“正在停止”短暂状态，可以复用 `waiting` 或新增 `cancelling`。

如果不想扩展数据结构，可以先用 `waiting` 表达“等待外部子任务停止”，并通过 `latestProgress` 说明。

### 6.8 提醒任务扩展为小型日程机制

短期：

1. 支持绝对时间，例如“明天早上九点提醒我”。
2. 支持用户时区显示和确认。
3. 应用启动时对错过提醒给出“刚才错过了这个提醒”的特殊文案。

中期：

1. 支持重复提醒。
2. 支持稍后提醒。
3. 支持编辑 `dueAt`。
4. 根据系统能力接入更可靠的系统级通知或登录启动补偿。

### 6.9 增加任务观测面板

可以在控制台任务详情里展示：

1. 当前状态、执行器、工作目录、安全模式。
2. 关键事件时间线。
3. 产物清单。
4. 最近错误。
5. 标准输出和标准错误摘要，或调试入口。

这会让任务失败更可修复，也能降低用户对“后台到底在干什么”的不确定感。

### 6.10 配置与安全界面产品化

任务命令行设置页当前只暴露启用、优先级、超时。建议逐步增加：

1. 工作目录展示与选择。
2. 命令可用性检测。
3. 安全沙盒和权限策略的解释与选择。
4. 执行器健康检查。
5. “测试运行一个空任务”的试运行。
6. 对 Codex 非 Git 仓库目录的即时提示。

## 7. 建议推进顺序

### 第一阶段：低风险收口

1. 抽出任务状态判断辅助函数，替换分散的活跃/终态判断。
2. 让聊天“开始任务”走后台启动。
3. 增加数据库条件更新，防止重复启动同一任务。
4. 给任务事件增加更稳定的 `summary` 字段或迁移策略。
5. 补充 `waiting` 的定义，决定是否继续保留。

### 第二阶段：产物与可观测性

1. 启用 `task_artifacts`。
2. 把结果附件从事件载荷推断改为产物查询。
3. 新增任务详情视图。
4. 收敛标准输出和标准错误的存储策略。
5. 增加执行器运行耗时和失败分类。

### 第三阶段：任务执行编排升级

1. 新增 `TaskExecutionCoordinator`。
2. 引入按用户档案队列和并发限制。
3. 支持重试、重新运行、继续。
4. 支持取消升级和取消超时。
5. 将自动运行、手动启动、未来插件触发统一到同一执行入口。

### 第四阶段：提醒与日程能力

1. 增强自然语言时间解析。
2. 支持稍后提醒、重复提醒和编辑提醒。
3. 增强错过提醒体验。
4. 评估系统级调度或登录启动补偿。

## 8. 推荐的近期最小改动包

如果只做一轮小而有价值的优化，建议选择以下组合：

1. 新增 `taskState.ts`：统一 `isActiveTask`、`isSettledTask`、`isRunnableTask`、`isCancelableTask`、`isDeletableTask`。
2. 修改 `TaskService.runTaskInBackground()`：用条件状态更新从 `queued` 抢占到 `running`，失败则不启动执行器。
3. 修改 `ChatOrchestrator.handleTaskControl(start)`：改为后台启动，不等待子任务完成。
4. 新增 `TaskArtifactRepository` 第一版，只记录文件产物，不改复杂界面。
5. 增加测试覆盖：重复启动、手动启动后立即返回、产物附件路径、已完成任务不能被普通修改隐式重开。

这组改动能同时降低竞态风险、统一用户体验，并为后续任务详情和产物管理铺路。
