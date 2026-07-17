# 主对话 Agent

> 主对话 Agent 也叫 chat-agent / 主 Agent。
> 它是 AuraBot 的常驻人格和系统编排中枢，运行在 Python 后端主进程内。
> 用户感知上始终只有一个 Aura、一个长期 session、一个主对话队列。

## 设计范式

主 Agent 采用 **状态机托底的事件驱动编排器** 范式。

它不是持续自主行动的 ReAct worker，也不直接执行长工具循环。主 Agent 负责理解事件、维护人格和记忆、决定路由、分配任务、整合结果、对用户说话；真正的工具执行和多步行动交给 runtime task agent。

推荐分层：

1. **确定性运行时层**：队列、状态机、打断、错误处理、事件落库。
2. **结构化决策层**：输入分类、任务类型判断、记忆写入判断、任务分配计划。
3. **回复生成层**：结合 SOUL、记忆、任务状态和渠道，生成自然语言、情绪和动作信号。

这样可以让主 Agent 保持可控、可中断、可解释，同时保留 Aura 的连续人格。

## 当前实现定位

本文档描述的是目标后端形态。

当前仓库里 `frontend/src/core/orchestration/ChatOrchestrator` 是 TypeScript 侧的同步编排雏形，已经具备输入分类、任务创建/控制、记忆召回、回复生成等主 Agent 方向的能力，但它还不等同于最终 Python 后端主 Agent：

- 当前入口主要是 `sendMessage()`，不是统一的后端全局事件队列。
- 当前 gate 主要区分 `chat`、`task`、`task_control`，而本文档中的 `reminder`、`memory_op`、`task_callback`、`notification` 是目标扩展分类。
- 当前记忆写入更偏当前消息触发，短期记忆断点、待总结片段和断电恢复队列仍应在后端形态中补齐。
- 当前任务回调还没有形成完整的统一 event 协议，后续应让任务进度、完成、失败、询问和 skill 候选都通过 task-manager 回到主 Agent。

迁移到 Python 后端时，应保留现有编排经验，但把主对话、任务、记忆和主动触发从前端 TypeScript core 迁到后端主进程。

## 最小实现 v0

第一个 Python Main Agent 只验证编排与记忆边界，不接真实模型、MCP 或真实任务
执行。它必须包含：

1. 一个按顺序消费 `UserMessageEvent` 的进程内队列。
2. 一个固定的 `main` session、按 profile 隔离的短期缓冲区和可持久化 pending 状态。
3. 对 Python `MemoryService` 的受控 recall / remember 调用；memory service 不自行触发写入。
4. `POST /main-agent/messages`、`GET /main-agent/status` 和最小 WebSocket 事件输出，具体字段以[运行时契约](../backend-runtime-contract.md)为准。
5. 可注入的确定性回复器，用于验证消息、记忆断点、重启恢复和错误路径。

当前 `backend/memory/api.py` 已提供记忆能力，但尚未在 `backend.main` 中挂载；
`frontend/src/core/orchestration/ChatOrchestrator` 仍是生产 MVP 的主编排器。v0 的
目标是证明 Python 能安全接管一条消息链路，而不是复制完整 TypeScript 功能。

## 职责边界

主 Agent 负责：

1. 接收桌面端、QQ、微信、飞书、scheduler、task-manager 的标准化事件。
2. 维护全局主对话队列，并串行处理事件。
3. 持有唯一长期 session 上下文和 SOUL 人格。
4. 管理主对话短期记忆、话题断点和待总结片段。
5. 在回复或决策前召回长期记忆，并决定是否写入长期记忆。
6. 判断输入是普通聊天、任务请求、任务控制、提醒创建、记忆操作还是系统回调。
7. 向 task-manager 创建任务、查询任务、打断任务、继续任务、中止任务。
8. 接收 task-manager 的进度、完成、失败、询问和 skill 候选。
9. 把任务结果、提醒结果、记忆结果转成用户能听懂的 Aura 语气。
10. 输出断句文本、情绪、Live2D 动作和可选 TTS 分句事件。
11. 把需要推送的事件交给 websocket-hub 或 connector-hub。

主 Agent 不负责：

1. 不直接维护前端 WebSocket。
2. 不直接执行 MCP tool call 的长循环。
3. 不作为独立 HTTP 服务暴露给前端。
4. 不让 task-agent、memory 模块或前端绕过它直接写长期记忆。
5. 不在多个渠道之间创建多个并行主 session。

## 输入事件

所有输入先被标准化为主 Agent 事件，再进入主对话队列。

```ts
type MainAgentEvent =
  | UserMessageEvent
  | TopicBreakpointEvent
  | SchedulerEvent
  | TaskManagerEvent
  | MemoryMaintenanceEvent
  | InterruptEvent;
```

### 用户输入

- 桌面控制台文字输入：跳过碎片门控，直接入队。
- 桌面 STT 转写输入：用户明确提交后直接入队。
- QQ / 微信 / 飞书输入：先经过 connector-hub 和 guard-agent，确认用户说完后入队。

### 话题断点

guard-agent 或主 Agent 发现话题切换时，提交 `TopicBreakpointEvent`。

断点只表示“上一段话题可以形成片段”，不等于立即写长期记忆。是否写入长期记忆仍由主 Agent 结合片段内容判断。

### scheduler 事件

- 到期提醒。
- cron 唤醒。
- 主动搭话。
- 记忆 follow-up。

scheduler 只负责触发事件，不直接生成对用户的话。

### task-manager 事件

- 任务已创建。
- 任务进度更新。
- 任务完成。
- 任务失败。
- 任务被打断。
- 任务需要用户补充信息。
- 任务返回可复用 skill 候选。

task-agent 不直接问用户；它的问题必须经 task-manager 交给主 Agent。

## 运行循环

主 Agent 全局只有一个消费循环。

```text
[事件入队]
    ↓
[按顺序取出最早事件]
    ↓
[加载 profile / soul / main session]
    ↓
[写入短期记忆或处理断点]
    ↓
[召回长期记忆和当前任务摘要]
    ↓
[结构化决策]
    ├─ 普通聊天
    ├─ 任务创建
    ├─ 任务控制
    ├─ 任务回调
    ├─ 提醒 / 主动触发
    └─ 记忆操作
    ↓
[执行对应业务分支]
    ↓
[生成 Aura 回复、情绪、动作、断句]
    ↓
[推送事件并落库]
    ↓
[必要时总结片段并写长期记忆]
```

主 Agent 可以流式输出回复，但同一时间只能处理一个主事件。新的用户输入可以进入队列，必要时也可以通过 `InterruptEvent` 打断当前回复。

## 状态机

```text
IDLE
  │
  ▼
DEQUEUING_EVENT
  │
  ▼
PROCESSING_INPUT
  │
  ├─ 普通聊天 ─────────→ GENERATING_REPLY ─→ STREAMING_REPLY ─→ FINALIZING
  │
  ├─ 任务创建 ─────────→ PLANNING_TASK ─→ DISPATCHING_TASK ─→ FINALIZING
  │
  ├─ 任务控制 ─────────→ CONTROLLING_TASK ─→ FINALIZING
  │
  ├─ 任务询问 ─────────→ ASKING_USER_FOR_TASK ─→ WAITING_FOR_USER
  │
  ├─ 提醒/主动事件 ────→ GENERATING_NOTIFICATION ─→ FINALIZING
  │
  ├─ 记忆维护 ─────────→ SUMMARIZING_MEMORY ─→ WRITING_MEMORY ─→ FINALIZING
  │
  ├─ 用户打断 ─────────→ INTERRUPTING ─→ IDLE
  │
  └─ 异常 ─────────────→ FAILED ─→ FINALIZING
  │
  ▼
IDLE
```

`WAITING_FOR_USER` 不阻塞整个系统长期停住。等待用户补充信息时，主 Agent 记录 pending 状态后回到 `IDLE`，后续用户回复会被路由回对应任务或确认流程。

## 启动依赖

```json
{
  "profile_id": "default",
  "soul": "当前角色人格配置",
  "main_session_store": "主 session 存储",
  "short_term_memory": "ShortTermMemoryBuffer",
  "memory_service": "MemoryService",
  "task_manager": "TaskManager",
  "scheduler": "Scheduler",
  "connector_hub": "ConnectorHub",
  "websocket_hub": "WebSocketHub",
  "skill_tool_registry": "SkillToolRegistry",
  "voice_service": "VoiceService，可选",
  "chat_model": "主回复模型",
  "gate_model": "输入分类和门控模型",
  "memory_model": "记忆抽取、总结、整理模型"
}
```

## 上下文构建

每次处理事件时，主 Agent 至少构造这些上下文：

- SOUL、语气边界和当前 profile。
- 当前事件内容、来源渠道、时间和时区。
- 最近主对话消息。
- 短期记忆中的当前话题摘要。
- 长期记忆召回结果，包括 `packed_context` 和可选 trace。
- 当前活跃任务摘要、等待用户确认的 pending 项。
- 可用任务类型、skill 组、tool 组摘要。
- 渠道约束，例如桌面控制台、移动 IM、TTS 分句需求。

上下文构建应遵循最小必要原则。debug trace 可以进入日志和调试面板，不默认进入用户可见回复。

## 结构化决策

主 Agent 不应让一个大 prompt 同时决定所有事情。建议拆成小的结构化决策：

### 输入分类

输出：

```json
{
  "input_kind": "chat | task_request | task_control | reminder | memory_op | task_callback | notification",
  "reply_decision": "reply | wait | ignore",
  "topic_ended": true,
  "confidence": 0.86
}
```

### 任务分配计划

当 `input_kind = task_request` 时，输出：

```json
{
  "task_type": "short | complex",
  "title": "任务标题",
  "description": "明确任务描述",
  "workspace": "可选工作区",
  "constraints": ["用户约束"],
  "expected_outputs": ["期望产物"],
  "skill_groups": ["建议技能组"],
  "tool_groups": ["建议工具组"],
  "needs_confirmation": false,
  "risk_level": "low | medium | high"
}
```

主 Agent 只做任务定义和分配，不进入任务执行循环。

### 记忆写入判断

输出：

```json
{
  "memory_action": "skip | defer | write | summarize_segment | forget | improve",
  "reason": "稳定偏好、未来事件、用户明确要求等",
  "source": "current_message | topic_segment | task_result | scheduler_event"
}
```

## 记忆工作流

### 短期记忆

主 Agent 维护 `ShortTermMemoryBuffer`。

- 用户输入、助手关键回复、任务结果摘要写入短期记忆。
- 桌面明确输入可以直接形成一轮消息；外部 IM 输入先由 guard-agent 合并碎片。
- 话题切换时调用 `mark_breakpoint()`。
- 断点片段需要持久化，避免应用重启或崩溃后丢失可总结片段。

### 记忆断点

断点用于把连续对话切成可总结片段。

触发来源：

1. guard-agent 判断外部渠道话题切换。
2. 主 Agent 判断当前回复已经结束一个话题。
3. 任务创建、任务完成、提醒创建等明确边界。
4. 上下文长度达到阈值，需要主动切片。

断点处理规则：

- 断点只切片，不自动写长期记忆。
- 片段进入待总结队列后，由主 Agent 在空闲或当前事件末尾处理。
- 片段总结必须保留来源 session、起止消息、时间范围和可追溯 id。
- 如果应用断电、崩溃或重启，未总结片段应能从持久化短期记忆恢复。

### 长期记忆写入

只有主 Agent 可以调用长期记忆写入。

可写入：

- 用户明确要求记住的信息。
- 稳定偏好、身份事实、人物关系。
- 有未来意义的事件、承诺、提醒。
- 对后续有帮助的任务结果摘要。
- 主动 follow-up 所需的时间线信息。

不写入：

- 寒暄和短期情绪波动。
- 未确认的推测。
- task-agent 原始日志。
- 大段工具输出。
- 对未来没有价值的临时上下文。

### 长期记忆召回

回复前，主 Agent 调用 `MemoryService.recall()`。

召回结果分三层使用：

1. `packed_context`：放入主回复上下文。
2. `memories`：用于可解释引用和调试面板。
3. `trace`：只进入 debug，不默认展示给用户。

### 遗忘与整理

用户要求删除、修改或整理记忆时，主 Agent 把自然语言转换为明确操作：

- `forget(memory_id | episode_id | query)`
- `improve(memory_id | patch)`
- `merge(memory_ids)`
- `resolve_conflict(memory_ids)`

无法唯一定位时，主 Agent 应先询问用户确认。

## 任务工作流

### 创建任务

当用户要求 AuraBot 执行工作时：

1. 主 Agent 分类为任务请求。
2. 提取任务描述、工作区、约束、风险和期望产物。
3. 判断任务类型：`short` 或 `complex`。
4. 查询 skill-tool-registry，选择技能组和工具组。
5. 对高风险、不可逆或权限不足的任务先询问用户确认。
6. 调用 task-manager 创建 runtime task agent。
7. 向用户说明任务已接收或已开始。
8. 将任务状态交给 websocket-hub 推送。

### 分配原则

- 简单聊天不要创建任务。
- 提醒类任务走 scheduler / reminder 逻辑，不启动 task-agent。
- 需要读写文件、跑命令、调用 MCP、生成产物的请求才创建 task-agent。
- task-agent 只拿到完成任务所需的最小上下文、技能组和工具组。
- 主 Agent 不把长期记忆库直接暴露给 task-agent；必要偏好应摘要进任务 payload。

### 任务控制

用户询问进度、要求暂停、继续、修改目标、重试、取消、打开结果时，主 Agent 调用 task-manager。

如果目标任务不明确：

1. 根据最近任务、活跃任务、用户描述进行候选匹配。
2. 唯一匹配则执行控制。
3. 多个候选则询问用户选择。
4. 无候选则用 Aura 语气说明没有找到。

### 任务向用户发问

task-agent 进入 `WAITING_FOR_INFO` 后，task-manager 把问题交给主 Agent。

主 Agent 负责：

1. 把任务问题改写成用户能理解的话。
2. 记录 pending task question。
3. 推送给用户。
4. 在用户回答后，把答案交回 task-manager。

task-agent 不直接接触用户渠道。

### 任务完成或失败

task-manager 把结果交回主 Agent。

主 Agent 决定：

- 如何告诉用户结果。
- 是否附带 artifact。
- 是否写入长期记忆。
- 是否提示用户确认保存 `new_skill_candidate`。
- 是否触发主动 follow-up。

任务失败时，主 Agent 不应暴露原始内部栈和 runner 细节；应说明失败点、可选下一步和是否可以重试。

## scheduler 和主动提醒

scheduler 只产生命中事件，不直接说话。

主 Agent 收到 scheduler 事件后：

1. 加载相关记忆、提醒或任务。
2. 判断是否仍然应该提醒。
3. 选择上一次用户交互渠道或用户指定渠道。
4. 生成短消息。
5. 推送 `notification.push`。
6. 必要时写入短期记忆或完成 follow-up 标记。

主动搭话必须可配置、可关闭，并尊重用户的免打扰设置。

## 回复输出

主 Agent 输出结构：

```json
{
  "message_id": "uuid",
  "session_id": "main",
  "channel": "desktop",
  "text": "回复文本",
  "segments": ["第一句。", "第二句。"],
  "emotion": "happy | calm | thinking | worried",
  "motion": "nod | wave | idle",
  "task_refs": ["task_id"],
  "memory_refs": ["memory_id"],
  "notification_refs": ["notification_id"]
}
```

回复文本需要断句。遇到完整句子时，可以先推送该句：

- 控制台可逐字或逐句显示。
- IM 渠道可按自然句发送。
- TTS 可在句末标点后立即进入合成，降低端到端延迟。
- Live2D 情绪和动作应随主回复状态变化，但不要让状态切换造成动作抖动。

## 事件输出

主 Agent 把事件交给 websocket-hub 或 connector-hub。

- `chat.message.started`
- `chat.delta`
- `chat.segment.completed`
- `chat.message.completed`
- `chat.message.failed`
- `agent.status.changed`
- `live2d.motion`
- `task.create_requested`
- `task.control_requested`
- `memory.segment_ready`
- `memory.write_requested`
- `notification.push`

## 错误处理

### 模型失败

- gate model 失败：降级到确定性规则分类。
- chat model 失败：向用户说明暂时无法生成回复。
- memory model 失败：跳过本轮写入，不阻塞回复。

### 任务失败

- task-agent 失败不等于主 Agent 失败。
- 主 Agent 负责把失败摘要转成用户可理解的结果。
- 可重试时提供重试入口。

### 记忆失败

- 召回失败时可以继续普通回复，但要记录事件。
- 写入失败不能让用户消息丢失。
- 待总结片段保留在队列中，稍后重试。

### 打断

用户打断当前回复时：

1. 停止当前模型流或后续断句推送。
2. 标记当前 assistant message 为 interrupted。
3. 保留已发送内容。
4. 新输入重新入队处理。

## 持久化要求

必须持久化：

- 主 session id 和消息历史。
- 短期记忆缓冲区和断点。
- 待总结片段。
- pending 用户确认和 pending task question。
- 任务引用和任务回调处理状态。
- 最近交互渠道。

这样应用重启后，主 Agent 可以恢复连续人格和未完成上下文。

## 硬性约束

1. 主 Agent 全局只有一个长期 session。
2. 主 Agent 串行处理主事件，不并发生成多个主回复。
3. 主 Agent 是编排器，不做长 ReAct 工具循环。
4. 只有主 Agent 可以直接读写长期记忆。
5. memory 模块不能自行决定把短期片段写入长期记忆。
6. task-agent 不直接读写长期记忆，也不直接向用户发问。
7. task-agent 的问题、结果、失败、skill 候选都必须经 task-manager 回到主 Agent。
8. scheduler 不直接生成用户话术，只向主 Agent 投递事件。
9. 前端不承载主对话、任务、记忆等核心业务逻辑。
10. 后端内部模块之间不通过本机 HTTP 二次转发。
11. 高风险、不可逆或权限不足操作必须先得到用户确认。
12. 主 Agent 输出给用户的话不能暴露内部实现词，例如 runner、payload、event、task id、模型提示。
