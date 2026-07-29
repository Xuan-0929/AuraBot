# 主对话 Agent

> 主对话 Agent 也叫 chat-agent / 主 Agent。
> 它是 AuraBot 的常驻人格和系统编排中枢，运行在 Python 后端主进程内。
> AuraBot 可以保存多个 profile；每个 profile 拥有独立的 LLM、声音模型、SOUL、
> 对话上下文和记忆存储，但运行时同一时间只能有一个 active profile。
> 用户感知上始终只有一个当前 Aura、一个 `main` session 和一个主事件调度器。

## 已确认的运行时契约

本节记录 2026-07-29 已确认的 Chat / Main Agent 需求。后文描述目标能力时必须遵守
这些约束；如旧的 v0 描述与本节冲突，以本节为准。

### Profile 与单 session

1. AuraBot 支持多个 profile，但任一时刻只能激活一个 profile。
2. 每个 profile 独立保存 chat LLM、gateway LLM、声音模型、SOUL、context SQLite、
   long-term memory SQLite、Qdrant 本地索引和 memory 协调锁。
3. profile 存储目录为应用安装目录下的 `profile/<profile_name>/`。`profile_name`
   是路径安全、全局唯一且创建后不可修改的存储标识；可另设可修改的
   `display_name`。
4. Main Agent 使用固定业务 session `main`。单 session 由 AuraBot 自己的
   context 维护机制实现，不依赖模型服务商的 thread/session。
5. profile 切换前必须同时满足：Main Agent 当前空闲、内存 event queue 为空、
   SQLite backlog 为空、active profile 没有任何非终态 Task。`queued`、
   `initializing`、`running` 和 `waiting` 都属于非终态。
6. 切换时卸载旧 profile 的 LLM、声音模型、SOUL、prompt context 和 memory service，
   再加载新 profile；不同 profile 的数据不能混入同一次模型调用。
7. 应用安装目录在部分打包平台可能不可写或被升级替换。启动 profile 前必须检查目录
   可写性和文件身份；失败时拒绝启动该 profile，不能静默改用另一目录。

### 两类队列

Main Agent 明确区分两类队列：

- `event_queue`：进程内事件调度队列，采用严格优先级，同优先级按服务端
  `accepted_seq` FIFO。
- `context_queue`：当前 profile 的持久化对话上下文，所有事实记录实时写入 SQLite；
  它不是只存在于内存中的 Python queue。

事件优先级从高到低为：

1. runtime control / shutdown；
2. voice 输入预留位，具体 EOS、预思考、抢占和 barge-in 协议尚未定稿；
3. 最终用户输入、Task question 的用户回答；
4. Task `waiting_for_info`；
5. Task `completed` / `failed` / `interrupted`；
6. scheduler 到期提醒；
7. proactive 主动搭话；
8. memory maintenance。

严格优先级不做 aging 或配额调度；只要高优先级持续存在，低优先级事件可以继续等待。
Task 普通 progress 只进入前端状态投影，不 invoke Main Agent。

每个已经 commit 的标准化事件必须先持久化为 Main turn，再尝试装入内存
`event_queue`。尚在 `wait` 的原始碎片只写 ingress 事实，不提前创建 turn。内存 queue
达到 capacity 后，新事件保持为 SQLite `queued` backlog；释放内存槽位后，scheduler
再按优先级和 `accepted_seq` 补入。重启时重新装载 `queued` turns；原
`processing` turns 标记为 `interrupted_by_restart`，保留已发送消息和工具记录，
但不自动重放。

调度支持严格高优先级事件对低优先级 turn 的安全点协作抢占：

1. 同优先级事件绝不抢占，仍按 `accepted_seq` FIFO。
2. 当前低优先级 turn 正在等待或流式接收 LLM 时，立即取消该 LLM invoke；尚未形成
   完整 Tool Call 的增量全部丢弃。
3. 已开始的 Tool Call 不强杀；完成并持久化结果后，不再执行该 turn 的后续 Tool 或
   LLM invoke。
4. 若被抢占 turn 尚未发布 `say_to_user`，也没有持久化 `tool_call.started`，同一
   `event_id` 原子退回 `queued`，保留原 `accepted_seq` 并增加 `attempt`。
5. 只要已经发布 say 或开始 Tool Call，turn 就标记为 `interrupted`，不自动重放，
   避免重复消息和重复副作用。

普通回复始终使用触发事件入队时绑定的 `reply_route`。外部渠道共享同一 profile 的
聊天历史。proactive 消息才使用用户指定渠道或最近成功交互渠道；不能在事件消费时读取
一个可能已经变化的全局 last-channel 来路由普通回复。

### 两级 Gateway

所有用户输入都经过 Gateway。Gateway 有两个逻辑判断，但外部输入只做一次合并 LLM
调用：

1. Completion Gate：判断用户的一串输入是否已经说完，输出
   `wait | commit | discard`。
2. Topic Gate：判断完整输入是否开启新话题，输出
   `same_topic | new_topic` 和置信度。

合并结果只有在 `completion = commit` 时才允许携带有效 `topic`；`wait` 或 `discard`
时 topic 不生效。桌面端手动提交的文字使用同一 Gateway 的 topic-only 模式，逻辑上
跳过 Completion Gate。QQ、微信等外部文字渠道先把碎片持久化并合并，再调用 combined
模式；每个新碎片重置静默计时器。模型持续返回 `wait`、调用失败或输出无法解析时，
最后一个碎片静默 5 秒后直接强制 `commit + same_topic`，不再追加一次 Topic-only
调用。5 秒为默认值，connector 可以覆盖。`discard` 只用于明确垃圾输入。

Topic Gate 处理所有渠道的完整输入。只有 `new_topic = true` 且置信度达到配置阈值时
才切换话题；低置信度、超时或解析失败统一按 `same_topic`。context 为空时无需封存。

### Turn、context 与封存

一轮对话定义为：

```text
User / System Event
→ 第一次 LLM invoke
→ 0..N 次 Tool Call / Tool Result / 后续 LLM invoke
→ finish
```

完整 Agent turn 才计为 context capacity 的一轮。一次 turn 内可以产生多条
`say_to_user` 消息，但仍只计一轮。`context_queue.capacity` 默认为连续 10 轮并可配置。
当已有完成轮数达到阈值，或 Topic Gate 判断当前输入开启新话题时，必须在处理当前输入
之前封存全部旧完成轮次；当前输入成为新 context 的第一轮。正在执行、尚未 finish 的
turn 不能被封存。

封存后的旧 context 进入 `to_be_deleted`，并生成可 claim 的 pending memory segment。
它不是立刻从 prompt 消失：只有长期记忆 SQLite 写入、Qdrant 索引和 segment ack
全部成功后，才能从后续 prompt 中移除。尚未完成的 `to_be_deleted` context 作为独立
pending-context section 继续注入，避免 memory 后台任务被取消时产生上下文断层。

### Prompt context 投影

Harness 不把 SQLite 中的全部 context 原样加载进内存。每次构建 prompt 时使用
确定性 projector：

- 保留当前事件、user messages 和所有已发出的 `say_to_user` assistant messages；
- 保留白名单工具由 MainAgentAdaptor 声明的必要结果；
- 排除逐轮 memory retrieve 结果、Gateway 决策、遥测、模型内部推理和非白名单
  tool result；
- 已完成 Task 的低价值过程信息不进入 prompt；只保留仍影响当前对话的结构化结果；
- 原始事实记录仍留在 SQLite，用于历史、审计和失败恢复。

`to_be_deleted` context 有独立 prompt token 预算。如果 pending context 因 Main
持续抢占 memory 而达到预算上限，下一 Main turn 在 invoke 前触发一次 memory
catch-up barrier：等待最旧 segment 完成 SQLite + Qdrant + ack，再重新构建 prompt。

### 自动记忆召回与锁交接

长期记忆 retrieve 不是 Main Agent 可调用的 Tool。每个完整 Main turn 只在第一次
LLM invoke 前由 runtime 自动 retrieve 一次，并把 `packed_context` 自动注入 prompt。
同一 turn 的后续 invokes 复用不可变 `memory_snapshot`，不重复抢锁。retrieve 失败时
立即释放锁，使用 active context 和 pending context 降级继续本轮。

retrieve query 只使用当前完整输入，不拼接近期对话，也不调用 query-rewrite LLM。
Chat 快路径硬性禁止 LLM、远程 embedding 和模型 reranker；只允许本地确定性
embedding、Qdrant Local、SQLite、规则融合和规则 rerank。retrieve 不设 deadline，
但同步实现必须在专用前台 executor 中运行，不能阻塞 Main asyncio event loop。

MemoryService 与 Qdrant Local 位于同一个 backend 进程。前台 retrieve 和后台 memory
使用独立 executor，并共享 active profile 的 MemoryCoordinator：

1. 后台 summary/extraction 基于 sealed segment 的不可变快照运行，不持 memory lock。
2. memory lock 不覆盖整个 segment，只覆盖一次不可拆的本地操作：SQLite transaction、
   单次 Qdrant outbox 操作或 segment ack。
3. Main 请求 retrieve 后，后台完成当前同步原子操作就让出锁，不再开始下一个阶段。
4. Main 持锁完成整次 retrieve；释放后，后台从持久化 phase 和 source id 幂等恢复。
5. 不强杀正在执行的同步线程，也不引入跨进程锁、本机 HTTP 或 RPC。

用户说“记住”“忘掉”或“改正”不是 Chat runtime 的特殊 memory command。Main 不拥有
任何 Memory Tool，系统提示词也不强制它作数据库式确认；它按照 SOUL 和当前关系语境
自由回应。memory 后台仍只在 context 封存后处理这些普通对话内容。

### `say_to_user`

Main Agent 给用户的文字必须通过 `say_to_user` Tool 产生。一个完整 turn 可以包含多次
LLM invoke，并且一次 invoke 可以同时返回 say 和其他 Tool Calls。runtime 必须先处理
say，再按模型声明顺序执行其他 Tool。

每次 `say_to_user` 都创建一条独立 assistant message，以 `turn_id`、`invoke_id` 和
`say_sequence` 关联同一 turn。消息一经发布不可覆盖。`say_to_user` 参数同时携带
Live2D motion / expression token；后端原样传给前端，不根据当前 manifest 拒绝或替换，
前端负责未知 token 容错。

模型若在同一次 invoke 返回多个 `say_to_user`，runtime 不重试，而是把文本按模型调用
顺序确定性合并为一条 assistant message；`motion_token` 和 `expression_token` 分别取
最后一个非空值。原始 say Tool Calls 全部落库，合并消息记录所有来源
`tool_call_id`。合并后的 say 仍必须先于其他 Tool 执行。

say message 与 delivery outbox 必须在同一 SQLite transaction 中写入。事务成功即向
Main 返回 Tool success；connector 异步至少一次投递，并使用 `message_id` 作为幂等键。
永久投递失败只更新 `delivery_failed`，不自动让 Main 重说。

允许模型在整个 turn 中从未调用 `say_to_user` 而静默 finish。runtime 仍必须把 turn
标记为 completed，不能让客户端永久停留在 thinking。

### Main Agent ToolGroup 与 MainAgentAdaptor

Main Agent 的 Tool 继续沿用 `Tool → ToolGroup → ToolGroupManager` 组织方式，不建立
第二套 Tool 注册模型。Main Agent 对象构造时写死它可以持有的低延迟会话
ToolGroups，并通过专用 MainAgentAdaptor 注册表记录每个 Tool 在 Main 场景下的特殊
协议，例如参数注入、timeout、结果是否进入 context 以及如何投影。

Main Agent 只直接持有低延迟会话能力，例如 `say_to_user`、短时只读查询、时间和
Task 创建/查询/控制。长耗时、多步、写文件、不可逆、需要权限升级或审批的操作必须
派发给 Task Agent。

Main Agent 的专用 ToolGroup 只能供 Main 自己使用：

1. Task 创建 Tool 给模型展示可分配 ToolGroups 时，必须从候选中移除全部
   Main-only ToolGroups。
2. 每次 Main invoke 只展示可分配 ToolGroup 的 `name + description`，不注入组内 Tool
   名称、参数 schema 或完整说明。
3. Task 创建 Tool 使用本次展示候选生成不可变 allowlist；payload 出现候选外 group
   时，整次 Tool Call 在 Main 层失败并让模型重新调用，不静默删除或透传。
4. TaskManager 不感知、保存或二次校验 Main-only 名单。
5. Main-only group 名单来自 Main Agent 构造配置；当前不要求修改 ToolGroup SQLite
   表增加 audience 字段。

Task ToolGroups 在 Task 创建时确定。Task 生命周期内保留同一个 Harness 和
CapabilitySession；`waiting → running` 只是同一 Harness 开启新的 LLM invoke，
不重新 acquire、解析或校验 ToolGroups。工具服务临时不可用属于 Tool Call failure，
不是授权变化。

### Main Agent Loop 与首响目标

两级 Gateway 后不再运行独立 intent classifier。Main 在首次 LLM invoke 中直接理解
聊天、Task 创建/控制、reminder 等意图，并通过 Tool Calls 表达决定。

非语音 turn 默认最多 10 次 LLM invoke，可配置，但不设置 turn 总墙钟 timeout。
每个 LLM 请求和 Tool 仍使用自己的 timeout。达到第 10 次 invoke 后不再发起第 11 次，
turn 直接进入 `completed` 并记录 `stop_reason = max_invocations`；不合成兜底回复。

一次 invoke 中，say 先执行，其他 Tool Calls 按声明顺序逐个执行。单个 Tool 失败或
超时只写该调用的结构化 `ToolError`，不会阻止后续调用；整批结果完成后进入下一次
LLM invoke。纯 assistant text、未知 Tool 或非法参数不直接展示；runtime 把结构化
协议错误反馈给下一 invoke，并计入 10 次上限。LLM transport、timeout 或响应解析异常
使当前 turn 进入 `failed`，Chat 层不自动重试，也不生成固定 say。

健康依赖和正常负载下，`time_to_first_decision` 的 P95 必须不超过 8 秒。起点是最后一个
输入片段到达并落库，终点是第一个完整可校验 Tool Call 或 terminal finish。另行记录
`first_tool_call_latency` 和 `silent_finish_rate`。指标必须拆分 Gateway、queue wait、
memory wait/retrieve、prompt build 和 Main LLM 耗时。

### Task 回调与 question

Harness 负责识别模型 `finish` 和 `question` 等语义 outcome，但不直接通知 Chat。
TaskManager 是 Task 状态 owner：它先提交 `completed`、`failed`、`interrupted` 或
`waiting`，再通过进程内 ChatEventSink 把带稳定 `task_event_id` 的已提交 snapshot
送入 Main Agent event queue。前端 SSE 与 ChatEventSink 共享同一 Task 事实来源，但
Chat 不通过本机 HTTP/SSE 回环消费事件。

Task 调用 question 后，TaskManager 将原 Harness 挂起，保留 Agent message/tool
上下文和 capability lease，但释放 Task 执行 slot。用户回答通过 `question_id`
交回 TaskManager；校验仍是当前 pending question 后，唤醒同一 Harness。

pending question 必须注入 Main prompt。Main 可以依据 SOUL、context 和 memory 自然
判断并直接替用户回答 Task，也可以调用 `say_to_user` 向用户询问。Main 自主代答不使用
数值 confidence 或第二个 Policy Gate，只记录 `answer_source = main`。Main 可以自主
批准高风险或不可逆操作，但不能伪造或绕过 OS、MCP、sandbox 或外部平台要求的真实
用户授权。

Task 创建时冻结 `task_reply_route`。question、completed、failed 和 interrupted 通知
在整个 Task 生命周期都发往创建渠道，不随以后从其他渠道的 Task 交互而改变。

只要 active profile 存在任何非终态 Task，就禁止切换 profile。

### 暂缓范围

语音 interim transcript、EOS、预思考、实时追加提示词、语音抢占和 barge-in 需要与
语音专项方案共同设计，当前不在本文中假定实现方式。

## 设计范式

主 Agent 采用 **状态机托底的事件驱动编排器** 范式。

它不是持续自主行动的 ReAct worker，也不直接执行长工具循环。主 Agent 负责理解事件、维护人格和记忆、决定路由、分配任务、整合结果、对用户说话；真正的工具执行和多步行动交给 runtime task agent。

推荐分层：

1. **确定性运行时层**：队列、状态机、打断、错误处理、事件落库。
2. **Gateway 决策层**：一次 combined 或 topic-only 模型调用完成输入提交和话题判断。
3. **Main Agent Loop**：结合 SOUL、context、memory 和 Task 状态，自主选择 say 与
   低延迟 Tools；不增加 intent classifier。

这样可以让主 Agent 保持可控、可中断、可解释，同时保留 Aura 的连续人格。

## 组件架构

采用同进程模块化单体。`MainChatManager` 是唯一应用门面，但保持轻量，只组合以下
组件：

1. `ChatIngressCoordinator`：持久化 connector 输入，合并片段并调用 Gateway。
2. `MainEventStore + PriorityScheduler`：SQLite 事实、内存严格优先队列、CAS claim、
   协作抢占和重启恢复。
3. `ContextStore + PromptProjector`：保存 turn 事实、context membership、封存状态和
   确定性 prompt 投影。
4. `MainHarness`：单消费者 Main LLM / Tool loop、协议校验和 invoke 上限。
5. `MemoryCoordinator`：自动 retrieve、前后台 executors、原子阶段锁和 segment
   handoff。
6. `TaskChatBridge`：直接消费 TaskManager committed snapshots，不消费前端 SSE。
7. `DeliveryOutbox`：持久化 say message，并把投递交给 connector-hub /
   websocket-hub。

`MainChatManager` 负责启动、关闭、active profile 装卸和组件编排，不吸收上述组件的
状态机逻辑。它由 `BackendRuntime` 创建和持有，并随 FastAPI lifespan 统一关闭。

## Profile 存储与最小管理

首期使用全新的 backend profile 存储，不读取或迁移 frontend demo 的 SQLite：

```text
<install-dir>/profile/
├── active_profile.json
└── <immutable-profile_name>/
    ├── profile.json
    ├── soul.md
    ├── chat.sqlite3
    ├── memory.sqlite3
    └── qdrant/
```

`profile.json` 保存 display name、Main/Gateway/sound model 配置和 secret reference，
不直接保存明文 API key。`active_profile.json` 使用临时文件和 atomic replace 更新。
profile root 为空时自动创建 `default`；缺少模型凭据时 backend 仍可启动，但该 profile
标记为 `unavailable`，Chat 拒绝准入并暴露诊断。

首期 Profile API 只包含 list、create 和 activate，不包含删除、重命名、复制、导入或
导出。create 不自动激活；activate 必须执行前述空闲、queue/backlog 和非终态 Task
检查。

## 当前实现与首期交付边界

当前 `backend/agent/chat/main_chat.py` 只有空的 `MainChat` 占位类；本设计不受既有 Chat
接口约束。第一阶段完成 Backend Chat 垂直切片：agent/runtime、profile stores、
Gateway、Main Loop、MemoryCoordinator、Task bridge、HTTP、WebSocket 和 backend
测试。

第一阶段明确不包含：

- frontend / Electron 改造或旧 `ChatOrchestrator` 兼容；
- frontend profile、message、memory 或 Task 数据迁移；
- 语音 interim、EOS、speculation、pre-thinking、barge-in 或 TTS 调度；
- Profile 删除、重命名、复制、导入和导出；
- 跨进程 memory、远程 embedding 或 model-based retrieve。

## 职责边界

主 Agent 负责：

1. 接收桌面端、QQ、微信、飞书、scheduler、task-manager 的标准化事件。
2. 维护全局主对话队列，并串行处理事件。
3. 持有 active profile 的唯一 `main` session 上下文和 SOUL 人格。
4. 管理持久化 `context_queue`、话题封存和待处理 memory segments。
5. 在第一次模型 invoke 前触发 runtime 自动召回，并把可封存 context 可靠交给
   profile-specific memory 后台流程。
6. 让 Main LLM 直接理解聊天、Task、提醒和系统回调，不增加 intent classifier。
7. 向 task-manager 创建任务、查询任务、打断任务、继续任务、中止任务。
8. 接收 task-manager 已提交的 waiting、完成、失败、打断和 skill 候选；普通 progress
   只进入状态投影。
9. 把 Task 结果和提醒转成用户能听懂的 Aura 语气。
10. 通过 `say_to_user` 输出用户可见文本、Live2D 动作/表情 token 和可选 TTS 输入。
11. 把需要推送的事件交给 websocket-hub 或 connector-hub。

主 Agent 不负责：

1. 不直接维护前端 WebSocket。
2. 不直接执行 MCP tool call 的长循环。
3. 不作为独立 HTTP 服务暴露给前端。
4. 不把长期记忆读写能力直接暴露给 Main 模型、task-agent 或前端；长期记忆写入只由
   profile-specific memory 后台按 segment 状态机执行。
5. 不同时激活多个 profile，也不在多个渠道之间创建多个并行主 session。
6. 不把长期记忆 retrieve、recall trace 或 memory maintenance 暴露为模型可调用 Tool。
7. 不把用户自然语言中的“记住/忘掉”提升为 Chat runtime memory command。

## 输入事件

所有输入先被标准化为主 Agent 事件，再进入主对话队列。

```ts
type MainAgentEvent =
  | UserMessageEvent
  | SchedulerEvent
  | TaskManagerEvent
  | MemoryMaintenanceEvent
  | InterruptEvent;
```

### 用户输入

- 桌面控制台手动文字输入：跳过 Completion Gate，但必须经过 Topic Gate。
- 桌面 STT 转写输入：必须经过 Completion Gate 和 Topic Gate；具体 interim / EOS
  语义等待语音专项方案。
- QQ / 微信 / 飞书输入：先由 connector-hub 持久化并合并碎片，经 Completion Gate
  确认 `commit` 后再进入 Topic Gate。

### 话题断点

Topic Gate 在完整新输入到达时判断是否开启新话题。只有高置信度
`new_topic` 才触发断点；失败和低置信度默认延续当前话题。

断点触发后，runtime 在处理当前新输入前把旧完成轮次原子转成 pending memory
segment，并把它们标记为 `to_be_deleted`。当前输入随后成为新 context 第一轮。

### scheduler 事件

- 到期提醒。
- cron 唤醒。
- 主动搭话。
- 记忆 follow-up。

scheduler 只负责触发事件，不直接生成对用户的话。

### task-manager 事件

- 任务完成。
- 任务失败。
- 任务被打断。
- 任务需要用户补充信息。
- 任务返回可复用 skill 候选。

task-agent 不直接问用户；它的问题必须经 task-manager 交给主 Agent。普通进度更新
只进入前端 SSE 状态投影，不作为 MainAgentEvent 入队。

## 运行循环

主 Agent 全局只有一个消费循环。

```text
[原始输入碎片幂等写入 SQLite]
    ↓
[外部输入执行一次组合 Gateway；桌面手动文字仅判断 Topic]
    ↓
[commit 后创建 turn 和 queued MainAgentEvent]
    ↓
[按严格优先级、同级 accepted_seq 取出事件]
    ↓
[加载 active profile / soul / main session]
    ↓
[必要时先封存旧 context]
    ↓
[构建确定性 prompt context]
    ↓
[在 memory 前台 executor 自动 retrieve；异常则降级]
    ↓
[执行 Main Agent Loop]
    ├─ say_to_user
    ├─ 低延迟会话 Tool
    ├─ Task 创建 / 控制
    └─ 静默 finish
    ↓
[完成并持久化 turn]
```

Task、scheduler、control 和 memory maintenance 事件不经过 Gateway。Main 同一时间
只处理一个事件；新输入仍可持久化并进入内存队列或 SQLite backlog。任何严格更高
优先级事件都可以按协作式抢占规则中断当前 turn；语音 interim、EOS、预思考和
barge-in 仍等待语音专项方案。

## 状态机

```text
IDLE
  │
  ▼
DEQUEUING_EVENT
  │
  ▼
BUILDING_CONTEXT
  │
  ▼
RETRIEVING_MEMORY
  │
  ▼
INVOKING_MAIN
  │
  ├─ Tool Calls ───────→ EXECUTING_TOOLS ───────────→ INVOKING_MAIN
  ├─ finish / 达上限 ──→ FINALIZING ────────────────→ IDLE
  ├─ 更高优先级事件 ───→ PREEMPTING ────────────────→ IDLE
  └─ 致命模型错误 ─────→ FAILED ────────────────────→ IDLE
  │
  ▼
IDLE
```

`WAITING_FOR_INFO` 是 TaskManager 中 Task 的状态，不是 Main 消费循环的阻塞状态。
Task 问题提交后，Main 可以自行回答，也可以向用户提问；Main turn 完成后始终回到
`IDLE`。

## 启动依赖

```json
{
  "active_profile": "唯一 active profile 的不可变运行时快照",
  "profile_store": "应用安装目录/profile/<profile_name>",
  "soul": "active profile 的角色人格配置",
  "main_session_store": "主 session 存储",
  "context_store": "active profile 的 context SQLite 与 prompt projector",
  "memory_service": "active profile 的 MemoryService、协调锁和前后台 executor",
  "task_manager": "TaskManager",
  "task_chat_bridge": "Task 已提交语义事件到 Chat 的进程内桥",
  "scheduler": "Scheduler",
  "connector_hub": "ConnectorHub",
  "websocket_hub": "WebSocketHub",
  "delivery_outbox": "用户消息的可靠异步投递器",
  "skill_tool_registry": "SkillToolRegistry",
  "main_tool_group_names": ["构造时固定的 Main-only 与低延迟 ToolGroups"],
  "main_agent_adaptors": "Main 场景专用 Adaptor 注册表",
  "voice_service": "VoiceService，可选",
  "chat_model": "active profile 的主回复模型",
  "gateway_model": "同一 profile 的组合 Completion + Topic 判断模型",
  "memory_model": "active profile 的记忆抽取、总结、整理模型"
}
```

## 上下文构建

每次处理事件时，主 Agent 至少构造这些上下文：

- active profile 的 SOUL、语气边界、固定 session id 和 profile identity。
- 当前事件内容、来源渠道、时间和时区。
- `context_queue` 的确定性 prompt 投影。
- 尚未完成 memory handoff 的 `to_be_deleted` pending context。
- runtime 自动 retrieve 得到的 `memory_snapshot.packed_context`。
- 当前活跃任务摘要和尚未回答的 pending task question。
- Main Agent 构造时固定的低延迟 Tool，以及当前可分配给 Task 的 skill / tool group
  名称和描述；不注入组内 Tool 摘要或 schema。
- 触发事件入队时冻结的 `reply_route` 和渠道约束。

逐轮 recall 结果、trace、Gateway 决策、内部推理、遥测和非白名单 tool result 不进入
后续 `context_queue` 投影。debug trace 只进入日志和调试面板。

## 模型接口

### Gateway 决策

外部输入由一次组合 Gateway 调用同时判断是否说完和是否开启新话题：

```json
{
  "completion": "wait | commit | discard",
  "topic": "same_topic | new_topic",
  "coalesced_text": "已经持久化并合并的输入",
  "completion_confidence": 0.86,
  "topic_confidence": 0.86,
  "reason": "仅用于诊断"
}
```

`topic` 仅在 `completion = commit` 时有效。桌面手动文字跳过 Completion，只让同一
Gateway 模型执行 Topic-only 调用：

```json
{
  "topic": "same_topic | new_topic",
  "confidence": 0.86,
  "reason": "仅用于诊断"
}
```

连续 `wait`、Gateway 异常或无法解析时，每个新碎片都重置 connector 静默计时器；
达到默认 5 秒（connector 可配置）后直接 `commit + same_topic`，不得再调用一次
Topic 模型。Topic 低置信度或失败也按 `same_topic`。

Main 不运行独立 intent classifier。第一次 Main LLM invoke 直接根据 prompt 和可用
Tool 决定聊天回复、创建/控制 Task、创建提醒或静默结束。创建 Task 时，Main 通过
Task Tool 传入任务定义、精确 ToolGroup 名称列表及事件冻结的回复渠道；是否需要
真正的系统权限仍由底层工具和授权边界决定。

## 记忆工作流

### `context_queue`

`context_queue` 是 active profile 的 SQLite 持久化上下文，不是进程内
`ShortTermMemoryBuffer`。用户输入、`say_to_user` 消息、白名单 Tool Result、Task
结果摘要和 turn 状态均先写事实表，再由确定性 projector 生成模型 prompt。

桌面手动文字提交可以直接形成候选 turn；其他输入先经过 Completion Gate 合并成完整
输入。完整 turn 达到 capacity，或 Topic Gate 高置信度判断新话题时，runtime 在处理
当前输入前封存旧完成轮次。

### Context 封存与 memory handoff

封存是“把全部旧完成轮次交给 memory”，不是滑动窗口逐轮淘汰：

1. runtime 在同一 profile SQLite 中创建带稳定 `segment_id` 的 pending memory
   segment，并把关联旧 context 标记为 `to_be_deleted`。
2. 同一 backend 进程中的 memory 后台 executor claim segment，完成总结、事实抽取、
   长期记忆 SQLite 写入和
   Qdrant 索引。
3. 长期记忆事务同时写 memory fact 与 vector outbox，确保 SQLite 成功但向量操作中断
   后可以重试。
4. SQLite、Qdrant 和 segment ack 全部成功后，projector 才停止注入该旧 context。
5. 重启后继续处理未 ack segment；不会因为进程终止丢失待整理上下文。

普通 Task 创建、Task 完成或 reminder 创建本身不构成 context 封存条件。当前封存条件
只有 capacity 和高置信度 `new_topic`。

### 长期记忆写入

长期记忆写入由 profile-specific memory 后台 executor 执行，不由 Main 模型调用
memory Tool。Chat 只交付 runtime 已封存、可追溯且不可变的 segment snapshot；
写入选择、事实抽取、冲突处理和长期记忆维护都是 memory 模块的内部职责。memory
后台不能任意读取其他 profile，也不能绕过 segment 状态机。

### 长期记忆召回

每个 Main turn 的第一次 LLM invoke 前，runtime 自动调用一次
`MemoryService.retrieve()`；Main 模型不拥有 retrieve/recall Tool。同一 turn 的后续
invoke 复用首次返回的不可变 `memory_snapshot`。

retrieve query 只使用当前已经 commit 的完整输入，不拼接最近 context。首期
`MemoryService` 必须使用本地确定性链路：本地规则 QueryAnalyzer、本地 hash
embedding、SQLite FTS/entity、Qdrant Local 和确定性排序；不得在 retrieve 路径调用
LLM、远程 embedding 或模型 reranker。

召回结果分三层处理：

1. `packed_context`：放入主回复上下文。
2. `memories`：用于可解释引用和调试面板。
3. `trace`：只进入 debug，不默认展示给用户。

同步 retrieve 在专用前台线程 executor 中执行，不阻塞 async loop，也不设置 Chat
层硬 deadline。前后台共享的锁只覆盖一个原子本地操作：SQLite transaction、单次
Qdrant outbox 操作或 segment ack，不覆盖整个 segment。Main 请求 retrieve 时，后台
完成当前原子操作后停止进入下一阶段并让出锁；Main retrieve 完成后，后台从持久化
phase 和 source id 继续。异常时记录诊断并 fail-open，不能丢失用户输入。

Main 没有任何 memory Tool。用户说“记住”“忘掉”或“修改记忆”时，Main 只把它当作
普通陪伴对话处理，不向用户承诺数据库已经发生变化；相关信息是否进入长期记忆，仍
由之后的 segment handoff 和 memory 模块内部策略决定。

## 任务工作流

### 创建任务

当用户要求 AuraBot 执行工作时：

1. Main LLM 在正常 invoke 中直接决定调用 Task Tool。
2. 提取任务描述、工作区、约束、风险和期望产物。
3. 判断任务类型：`short` 或 `complex`。
4. Main 查询 skill-tool-registry，只把可分配 ToolGroup 的名称和描述展示给模型；
   Main-only ToolGroups 由 Main 层写死并过滤。
5. Main 以本次展示的精确、不可变 allowlist 校验 Tool Call；包含未知或未展示 group
   时整个调用失败并进入下一次 Main invoke，不静默剔除。
6. 调用 task-manager 创建 runtime task agent，并冻结创建事件的回复渠道。
7. 需要时通过 `say_to_user` 向用户说明任务已接收或已开始。
8. TaskManager 把状态作为独立投影交给前端 SSE。

### 分配原则

- 简单聊天不要创建任务。
- 提醒类任务走 scheduler / reminder 逻辑，不启动 task-agent。
- 需要读写文件、跑命令、调用 MCP、生成产物的请求才创建 task-agent。
- task-agent 只拿到完成任务所需的最小上下文、技能组和工具组。
- Main-only 是 Main 层写死的展示和调用约束；TaskManager 不知道、也不二次拒绝
  Main-only ToolGroups。
- Task 创建时分配的 ToolGroups 和 CapabilitySession 在整个 Task 生命周期内固定。
  `WAITING_FOR_INFO` 恢复时沿用同一 Harness，并进入一轮新的 LLM invoke；不重新
  校验、不重新获取也不换发能力。某个 Tool 临时不可用时，该次 Tool Call 按失败处理。
- 主 Agent 不把长期记忆库直接暴露给 task-agent；必要偏好应摘要进任务 payload。

### 任务控制

用户询问进度、要求暂停、继续、修改目标、重试、取消、打开结果时，主 Agent 调用 task-manager。

如果目标任务不明确：

1. 根据最近任务、活跃任务、用户描述进行候选匹配。
2. 唯一匹配则执行控制。
3. 多个候选则询问用户选择。
4. 无候选则用 Aura 语气说明没有找到。

### 任务向用户发问

Harness 返回 `question` outcome 后，task-manager 先原子提交 Task 为
`WAITING_FOR_INFO`，生成稳定 `task_event_id`，再通过 ChatEventSink 把 committed
snapshot 交给主 Agent。

主 Agent 负责：

1. 把 committed task question 和上下文注入当前 Main prompt。
2. 记录带 `question_id` 的 pending task question。
3. 自行给出答案，或通过 `say_to_user` 把问题改写成用户能理解的话。
4. 将 Main 自行给出的答案以 `answer_source = main` 记录并交回 task-manager；若向
   用户提问，则在用户回答后按 `question_id` 交回答案。

task-agent 不直接接触用户渠道。等待期间同一 Harness 保持挂起，保留 message/tool
上下文和 capability lease，但释放执行 slot；收到有效答案后恢复同一 Harness。
Main 可以自行批准高风险或不可逆的 Task 问题；但它不能伪造或绕过操作系统、MCP、
sandbox 或外部系统真正要求的授权。Task question 和 terminal 通知始终发往 Task
创建时冻结的渠道，不随之后的用户交互渠道变化。

### 任务完成或失败

task-manager 必须先提交 terminal 状态，再通过 ChatEventSink 把同一已提交 snapshot
交回主 Agent。前端 SSE 是同一 Task 事实的另一个投影，不是 Chat 的内部消费协议。
Main 以稳定 `task_event_id` 去重。

主 Agent 决定：

- 如何告诉用户结果。
- 是否附带 artifact。
- 是否把对后续对话有价值的结构化摘要保留在 context；长期记忆仍由 memory handoff
  流程处理。
- 是否提示用户确认保存 `new_skill_candidate`。
- 是否触发主动 follow-up。

任务失败时，主 Agent 不应暴露原始内部栈和 runner 细节；应说明失败点、可选下一步和是否可以重试。

## scheduler 和主动提醒

scheduler 只产生命中事件，不直接说话。

主 Agent 收到 scheduler 事件后：

1. 加载相关记忆、提醒或任务。
2. 判断是否仍然应该提醒。
3. 主动消息选择用户配置渠道，未配置时使用最近一次成功交互渠道。
4. 生成短消息。
5. 推送 `notification.push`。
6. 必要时写入 `context_queue` 或完成 follow-up 标记。

主动搭话必须可配置、可关闭，并尊重用户的免打扰设置。

## 回复输出

每次成功执行 `say_to_user` 都创建一条独立、不可覆盖的 assistant message：

```json
{
  "message_id": "uuid",
  "turn_id": "uuid",
  "invoke_id": "uuid",
  "say_sequence": 1,
  "session_id": "main",
  "profile_id": "uuid",
  "reply_route": {
    "connector": "desktop",
    "conversation_id": "origin-conversation"
  },
  "text": "回复文本",
  "motion_token": "来自 active Live2D 模型声明",
  "expression_token": "来自 active Live2D 模型声明",
  "task_refs": ["task_id"],
  "source_tool_call_ids": ["参与当前消息的 say_to_user Tool Call 稳定 id"],
  "delivery_state": "pending | delivered | failed"
}
```

同一 turn 可以产生多条 message，并用 `turn_id`、`invoke_id`、`say_sequence` 排序关联。
同一 invoke 中的多个 say 按调用顺序合并；文本依序拼接，motion / expression 分别取
最后一个非空值，原始 Tool Calls 全部保留。
普通消息使用触发事件入队时冻结的 `reply_route`；proactive 消息才动态选择配置渠道或
最近成功渠道。后端不校验或替换 Live2D token，前端必须对未知 token 容错。

message 和 delivery outbox 在同一 SQLite transaction 提交。数据库提交成功就是
`say_to_user` Tool success；connector 之后以 `message_id` 幂等、至少一次投递。永久
投递失败只把 message 标为 `failed`，不触发 Main 自动重说。

## 事件输出

主 Agent 把事件交给 websocket-hub 或 connector-hub。

- `chat.turn.started`
- `chat.turn.completed`
- `chat.turn.interrupted`
- `chat.turn.failed`
- `chat.message.completed`
- `chat.message.delivery_failed`
- `agent.status.changed`
- `live2d.motion`
- `task.create_requested`
- `task.control_requested`
- `memory.segment_ready`
- `notification.push`
- `profile.activated`

## 公共接口

首期提供 backend-only 的稳定接口，不兼容 frontend demo 的旧 ChatOrchestrator
contract：

### HTTP

- `POST /chat/inputs`：接收手动文字或 connector 已标准化输入。active profile 由服务端
  绑定，调用方不能任意指定 profile。成功持久化后返回 `202 Accepted` 和 receipt；
  Gateway、排队和 Main 处理异步执行。
- `GET /chat/status`：返回 active profile 可用性、Main 状态、queue/backlog 深度和最近
  一次失败诊断，不返回模型内部推理。
- `GET /chat/main-session`：分页读取当前 active profile 的 `main` session 消息事实。
- `GET /profiles`：列出 profile 及 available/unavailable 状态。
- `POST /profiles`：创建不自动激活的新 profile。
- `POST /profiles/{name}/activate`：仅在 Main、队列/backlog 和全部 Task 满足切换条件
  时原子切换；否则返回冲突及具体阻塞项。

### WebSocket

- `WS /ws`：投影 chat message、turn、agent status、Live2D、Task 和 notification 事件。
  WebSocket 是客户端投影协议，不是 Chat 与 TaskManager 的内部通信协议。
- 重连方使用稳定 event/message id 自行去重；服务端事实仍以 SQLite、TaskManager 和
  delivery outbox 为准。

### 幂等边界

1. ingress：`(profile_id, source_type, source_event_id)` 唯一；同键同 payload 返回原
   receipt，同键不同 payload 返回 conflict。
2. Main event：processing lease + CAS 保证同一事件只有一个活跃 consumer。
3. say：原始 `tool_call_id` 与最终 `message_id` 去重。
4. Task bridge：稳定 `task_event_id` 去重。
5. memory：稳定 `segment_id/source_id`、vector outbox 与 ack 去重。

## 错误处理

### 模型失败

- 组合 Gateway 失败或无法解析：继续等待；达到 connector 静默 timeout 后强制
  `commit + same_topic`，不追加 Topic 调用。
- Topic 失败或低置信度：按 `same_topic` 继续。
- Main LLM transport、timeout 或响应解析失败：当前 turn 标记 `failed`；Chat 层不
  自动重试，也不合成固定 `say_to_user`。
- memory retrieve 失败：使用 active/pending context 降级继续本轮。
- memory 后台 executor 处理失败：保留 segment 和 `to_be_deleted` context，稍后重试。

### 任务失败

- task-agent 失败不等于主 Agent 失败。
- 主 Agent 负责把失败摘要转成用户可理解的结果。
- 可重试时提供重试入口。

### 记忆失败

- 召回失败时可以继续普通回复，但要记录事件。
- 写入失败不能让用户消息丢失。
- 待总结片段保留在队列中，稍后重试。

### 打断与抢占

任何严格更高优先级事件到达时，scheduler 执行安全点协作抢占：

1. 正在进行的低优先级 LLM invoke 立即取消，未形成完整 Tool Call 的 delta 丢弃。
2. 已经开始的 Tool Call 不强杀；结果持久化后停止当前 turn。
3. 尚未 say 且没有 `tool_call.started` 时，保留原 `accepted_seq` 原子重排同一事件。
4. 已经 say 或开始 Tool 后，保留已产生事实并把 turn 标记 `interrupted`，不重放。

语音 interim、EOS、预思考、抢占和 barge-in 的精确状态转换暂缓到语音专项设计。

## 持久化要求

必须持久化：

- profile 与固定业务 session `main` 的消息历史。
- Main turn 的 `queued | processing | completed | interrupted | failed |
  interrupted_by_restart` 状态、`accepted_seq`、`attempt`、priority、processing lease、
  `stop_reason` 和冻结的 `reply_route`。
- `context_queue`、封存关系、`to_be_deleted` 状态和 pending memory segment。
- `say_to_user` 的原始 Tool Calls、合并 message、`invoke_id`、`say_sequence` 和
  delivery outbox 状态。
- memory fact、处理 phase、source id、vector outbox、Qdrant ack 和 segment ack。
- pending task question、任务引用和任务回调处理状态。
- ingress、STT、Main event、say、Task event 和 memory source 的去重键。
- 最近成功交互渠道，仅供 proactive 路由。
- profile metadata 和原子更新的 active profile pointer。

这样应用重启后，主 Agent 可以恢复连续人格和未完成上下文。

## 首期验收

实现至少覆盖以下自动化测试和观测验证：

1. **Profile 隔离**：default 自动创建；无凭据时 backend 可启动但 Chat 拒绝准入；
   profile 数据、SOUL、模型配置、context 和 memory 不串库；任一非终态 Task 或非空
   queue/backlog 都阻止切换。
2. **Gateway**：外部碎片只做一次 combined 调用；桌面手动文字只做 topic-only；
   每个碎片重置计时器；5 秒 fallback 直接 `commit + same_topic`；重复 source event
   不产生重复 turn。
3. **调度和抢占**：严格 priority、同级 FIFO、无 aging；queue 满时进入 SQLite
   backlog；更高优先级事件可取消 LLM；已启动 Tool 不被强杀；只有零副作用 turn 才按
   原 accepted sequence 重排。
4. **Context**：完整 turn 才计数；第 10 轮或 `new_topic` 在当前输入前封存全部旧轮次；
   pending context 在 memory SQLite、Qdrant、ack 全成功前持续进入 prompt；投影不泄漏
   retrieve、Gateway、reasoning、telemetry 或非白名单 Tool Result。
5. **Memory**：每 turn 仅在首次 invoke 前 retrieve 一次，query 只有当前完整输入；
   后续 invoke 复用 snapshot；retrieve 路径不调用 LLM/远程服务；前后台 executor 按
   原子操作交锁；异常 fail-open，segment 可按 persisted phase 幂等恢复。
6. **Main Loop**：say 总先执行，其他 Tool 按模型顺序执行；单 Tool 失败不跳过后续
   Tool；协议错误进入下一 invoke；第 10 次后以 `max_invocations` 完成；LLM 致命错误
   标记 failed 且不自动重试/说兜底话。
7. **输出可靠性**：同 invoke 多个 say 正确合并；message/outbox 原子提交；
   `message_id` 幂等至少一次投递；永久失败不触发重说；普通消息固定 origin route，
   proactive 才使用配置/最近成功渠道。
8. **Task bridge**：TaskManager 先提交再通知；progress 不 invoke Main；question 可由
   Main 自答或转问用户；waiting 恢复同一 Harness 并开始新 invoke，不重校验能力；
   question/terminal 始终使用 Task 创建渠道。
9. **恢复**：重启重新加载 queued turns；processing turn 标记
   `interrupted_by_restart` 且不重放；delivery outbox 和 memory phase 继续处理。
10. **SLO**：健康依赖和正常负载下，采样验证 `time_to_first_decision` P95 ≤ 8 秒，并
    能分别观察 Gateway、queue、memory、prompt build、Main LLM、首个 Tool Call 和
    silent finish。

## 硬性约束

1. 运行时只能有一个 active profile 和一个业务 session `main`。
2. 主 Agent 串行消费事件；严格优先级、同优先级 `accepted_seq` FIFO，不做 aging。
3. 所有输入先持久化；内存 queue 满时保留 SQLite backlog。
4. 任何严格更高优先级事件都可协作抢占；只重排尚未 say 且没有启动 Tool 的 turn。
5. 普通回复使用事件冻结的 origin `reply_route`；只有 proactive 使用配置/最近渠道。
6. Main 是低延迟会话编排器，不执行长耗时、多步 ReAct 工作；意图由第一次 Main
   invoke 直接理解，不增加 intent classifier。
7. Main Tool 继续由 ToolGroupManager 组织；Main-only 名单由 Main 构造时写死，只由
   Main 层过滤和 enforce，TaskManager 不知道也不二次拒绝。
8. Task 创建只能使用本次展示的精确不可变 ToolGroup allowlist；未知 group 使整个
   Tool Call 失败，不能静默剔除或透传。
9. Task ToolGroups、CapabilitySession 和 Harness 在 Task 全生命周期固定；
   `waiting` 恢复是同一 Harness 的新 LLM invoke，不重新校验或换发能力。
10. `say_to_user` 是 Main 唯一用户可见输出协议且总是先执行；同 invoke 多个 say 必须
    确定性合并，不能以“最多一次”为前提拒绝。
11. say message 与 delivery outbox 原子写入；普通投递使用 `message_id` 幂等且至少
    一次，永久失败不自动重说。
12. memory retrieve 由 runtime 每 turn 自动执行一次，不是 Main Tool；query 只有当前
    完整输入，首期快路径禁止 LLM、远程 embedding 和模型 reranker。
13. Main 不拥有任何 memory read/write/control Tool；memory 写入只由同进程后台
    executor 按 sealed segment 状态机执行。
14. `to_be_deleted` context 只有在长期记忆 SQLite、Qdrant 和 segment ack 全成功后
    才能移出 prompt。
15. task-agent 不直接读写长期记忆，也不直接接触用户渠道。
16. Task question/terminal event 必须由 TaskManager 先提交状态，再经 ChatEventSink
    通知 Main；Chat 不回环消费前端 SSE。
17. Main 可以自主回答和批准 Task 的高风险/不可逆问题，但不能绕过 OS、MCP、
    sandbox 或外部平台真正的授权机制。
18. scheduler 不直接生成用户话术，只向 Main 投递事件。
19. 前端不承载主对话、Task、memory 等核心业务状态机；后端内部模块不通过本机 HTTP
    二次转发。
20. 非语音 Main turn 默认最多 10 次 LLM invoke，无 turn 总墙钟 timeout；达到上限
    静默 completed，LLM 致命错误 failed，二者都不生成固定兜底 say。
21. 语音 speculation、EOS、抢占和 barge-in 在专项方案确认前不得自行假设。
22. Main 面向用户的说法不得泄漏 runner、payload、内部 event、模型 prompt 等实现
    细节；需要引用 Task 时使用用户可理解的名称，不直接倾倒内部标识或栈信息。
