# 主对话Agent

> 主对话 Agent 也叫 chat-agent / 主 Agent。
> 它运行在 Python 后端主进程内，伴随应用生命周期长驻。
> 用户感知上始终只有一个主 session；所有渠道输入都进入同一个队列，串行处理。

## Agent职责

1. 接收来自桌面端、QQ、微信、飞书、scheduler、task-manager 的触发事件。
2. 维护全局主对话队列，按顺序处理，主 Agent 自身不并发。
3. 持有唯一长期 session 上下文，构造带 SOUL 语气的回复。
4. 管理主对话短期记忆，接收话题断点，并在需要时把断点间片段总结为长期记忆。
5. 读写长期记忆；其他 Agent 不直接写长期记忆。
6. 在回复前召回相关长期记忆，把 `packed_context` 放入主对话上下文。
7. 判断用户输入是普通聊天、任务请求、任务控制、提醒创建还是记忆相关操作。
8. 向 task-manager 创建任务、查询任务、打断任务、继续任务或中止任务。
9. 接收 task-manager 返回的任务结果或询问，并决定如何回复用户、是否写入记忆、是否生成 skill 候选。
10. 对回复做断句，通过 connector-hub 推回对应渠道。
11. 输出 Live2D 情绪和动作控制信号，由 websocket-hub 推给前端。

## 对外暴露的接口和事件描述

主 Agent 本身不直接对前端暴露独立 HTTP 或 WebSocket。

前端只调用后端主进程的 api-layer；api-layer、connector-hub、scheduler、task-manager 通过 Python 方法调用或队列向主 Agent 投递事件。

### 接口

- 提交用户输入。接收标准化后的用户消息，进入主对话队列。

- 提交话题断点。guard-agent 检测到话题切换时，为上一段话题结尾打断点。

- 提交 scheduler 触发。到期提醒、cron 任务、主动搭话事件进入主对话队列。

- 提交 task-manager 事件。任务进度、任务完成、任务失败、任务询问、任务中断结果交回主 Agent。

- 查询主 Agent 状态。返回当前是否空闲、正在处理的触发来源、当前队列长度、当前任务关联信息。

- 打断当前回复。停止当前回复生成或后续断句推送，等待新的主对话输入。

### 主 Agent 事件

主 Agent 不直接持有前端 WebSocket。它把事件交给 websocket-hub 或 connector-hub。

- `chat.message.started`：开始处理一条主对话输入。
- `chat.delta`：回复增量文本。
- `chat.message.completed`：回复完成。
- `chat.message.failed`：回复失败。
- `live2d.motion`：情绪、表情、动作控制信号。
- `task.create_requested`：请求 task-manager 创建任务。
- `task.control_requested`：请求查询、打断、继续或中止任务。
- `memory.segment_ready`：短期记忆断点片段需要总结。
- `memory.write_requested`：主 Agent 决定写入长期记忆。
- `notification.push`：主动提醒或主动搭话输出。

## 输入来源

1. 桌面端文字输入：跳过碎片门控，直接进入主 Agent 队列。
2. STT 转写输入：桌面端明确提交时直接进入主 Agent 队列。
3. QQ / 微信 / 飞书输入：先经过 connector-hub 和 guard-agent，再进入主 Agent 队列。
4. scheduler 事件：到期提醒、cron、倒计时、主动搭话。
5. task-manager 事件：任务完成、任务失败、任务进度、任务向用户提问。

## 处理流程

```
[接收触发事件]
    ↓
[入主对话队列]
    ↓
[按顺序取出最早事件]
    ↓
[写入短期记忆 / 标记断点]
    ↓
[召回长期记忆]
    ↓
[判断输入类型]
    ├─ 普通聊天 → 生成回复
    ├─ 任务请求 → 创建 runtime task agent
    ├─ 任务控制 → 调用 task-manager 控制接口
    ├─ 任务回调 → 回复用户 / 决定是否写记忆
    └─ 到期提醒 → 主动提醒用户
    ↓
[输出回复文本、情绪、动作]
    ↓
[断句并推送到对应渠道]
    ↓
[必要时总结短期片段并写长期记忆]
```

## 状态机

```
IDLE
  │
  ▼
PROCESSING_INPUT
  │
  ├── 需要创建任务 ──→ DISPATCHING_TASK ──→ IDLE
  │
  ├── 等待模型回复 ──→ GENERATING_REPLY ──→ STREAMING_REPLY ──→ IDLE
  │
  ├── 等待用户确认 ──→ WAITING_FOR_USER
  │
  ├── 收到任务询问 ──→ ASKING_USER_FOR_TASK
  │
  ├── 短期片段需要总结 ──→ SUMMARIZING_MEMORY ──→ WRITING_MEMORY
  │
  └── 发生错误 ──→ FAILED ──→ IDLE
```

## 启动时依赖

```json
{
  "profile_id": "default",
  "soul": "当前角色人格配置",
  "memory_service": "MemoryService",
  "short_term_memory": "ShortTermMemoryBuffer",
  "task_manager": "TaskManager",
  "scheduler": "Scheduler",
  "connector_hub": "ConnectorHub",
  "websocket_hub": "WebSocketHub",
  "skill_tool_registry": "SkillToolRegistry",
  "chat_model": "主对话模型",
  "gate_or_classifier": "输入分类 / 任务分类 / 记忆写入判断模型"
}
```

## 上下文构建

主 Agent 每次处理输入时，至少构造以下上下文：

- SOUL 和说话风格。
- 当前用户输入。
- 最近主对话上下文。
- 短期记忆中的最近话题。
- 长期记忆召回结果，包括 `packed_context` 和可选 trace。
- 当前进行中的任务摘要。
- 可用的任务能力、技能组和工具组摘要。
- 当前渠道信息，用于回复风格和推送目标。

## 记忆工作流

### 1. 短期记忆

主 Agent 维护 `ShortTermMemoryBuffer`。

- 每次用户输入和助手关键回复写入短期记忆。
- guard-agent 或主 Agent 判断话题结束时调用 `mark_breakpoint()`。
- 当断点数或上下文长度达到限制时，短期记忆返回最早的断点间片段。
- 该片段不能由 memory 模块自己写长期记忆，必须交给主 Agent 总结。

### 2. 长期记忆写入

主 Agent 总结短期片段或任务结果后，才调用 `MemoryService.remember()`。

写入来源包括：
- 用户明确表达的稳定偏好、事实、人物关系。
- 有未来意义的事件或提醒。
- 任务完成后对未来有帮助的结果摘要。
- 用户明确要求记住的内容。

不应写入：
- 寒暄。
- 纯临时上下文。
- task-agent 未经过主 Agent 判断的原始执行日志。

### 3. 长期记忆召回

回复前，主 Agent 调用 `MemoryService.recall()`。

召回结果中的：
- `memories` 用于可解释引用。
- `packed_context` 用于放入主对话 prompt。
- `trace` 用于 debug，不默认展示给用户。
- `source_episodes` 用于追溯和后续遗忘。

### 4. 遗忘与整理

用户要求删除或修改记忆时，主 Agent 调用：
- `MemoryService.forget()`
- `MemoryService.improve()`

主 Agent 负责把用户自然语言请求转换为明确的 memory ids、episode ids 或整理动作。

## 任务工作流

### 1. 创建任务

当用户请求 AuraBot 帮忙执行工作时，主 Agent：

1. 判断任务类型。
2. 提取任务描述、工作区、约束和期望输出。
3. 查询 skill-tool-registry，选择技能组和工具组。
4. 调用 task-manager 创建 runtime task agent。
5. 向用户确认任务已开始，并把任务状态交给 websocket-hub 推送。

### 2. 查询和控制任务

用户询问任务进展、要求暂停、继续、修改目标或取消时，主 Agent 调用 task-manager。

任务控制包括：
- 查询任务。
- 打断任务。
- 继续任务。
- 中止任务。

### 3. 任务向用户发问

task-agent 不能直接问用户。

任务线程进入 `WAITING_FOR_INFO` 后，通过 task-manager 把问题交给主 Agent。主 Agent 负责用自然语言询问用户，并在用户回答后把答案交回 task-manager。

### 4. 任务完成

任务结束后，task-manager 把结果交回主 Agent。

主 Agent 决定：
- 如何向用户说明结果。
- 是否把任务结果写入长期记忆。
- 是否把 `new_skill_candidate` 交给 skill-tool-registry 或提醒用户确认。

## 回复输出

主 Agent 输出不只是一段文本，还包括：

```json
{
  "message_id": "uuid",
  "channel": "desktop",
  "text": "回复文本",
  "emotion": "happy",
  "motion": "nod",
  "task_refs": ["task_id"],
  "memory_refs": ["memory_id"]
}
```

回复文本需要断句。遇到完整句子时，可以先通过 connector-hub / websocket-hub 推送，降低 TTS 和前端展示延迟。

## 硬性约束

1. 主 Agent 全局只有一个长期 session。
2. 主 Agent 串行处理输入，不并发生成多个主回复。
3. 只有主 Agent 可以直接读写长期记忆。
4. task-agent 不直接读写 memory，也不直接向用户发问。
5. memory 模块不自己决定把短期片段写入长期记忆。
6. 前端不承载主对话、任务、记忆等核心业务逻辑。
7. 后端内部模块之间不通过本机 HTTP 二次转发。
