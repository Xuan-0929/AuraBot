# 任务处理 Agent

> 任务处理 Agent 也叫 runtime task agent / task-agent。
> 它由 task-manager 按任务创建，即用即销毁，负责执行具体工作、上报进度和返回结果。
> 它不是 Aura 本体，不直接面向用户，也不直接读写长期记忆。

## 设计范式

Task Agent 采用 **按任务创建的运行时执行者** 范式。

主 Agent 负责理解用户、维护人格、管理记忆、定义任务和向用户表达；task-agent 只负责执行被分配的任务。所有任务事件必须经 task-manager 回到主 Agent，再由主 Agent 决定如何告诉用户、是否写记忆、是否保存 skill 候选。

任务执行分两类：

1. **短任务：ReAct 单循环**
   - 适合 1-10 步、低风险、短时间工作。
   - 不提前生成长计划，直接 Reason -> Act -> Observe。
   - 有严格工具调用、重试和时间上限。

2. **复杂任务：Plan-and-Execute + checkpoint**
   - 适合 1-30 分钟、需要理解代码库或分阶段产出的工作。
   - 先生成执行计划，再按阶段执行。
   - 每个阶段都要 checkpoint、上报进度，并允许打断、失败式恢复或重试。

## 当前实现定位

本文档描述的是目标后端形态。

当前仓库有两类过渡实现：

1. TypeScript core 仍承担当前 MVP 的任务执行：默认 deterministic runner，显式配置后可调用 Codex、Claude Code 或 Hermes CLI runner；它能记录进度、取消 child process，并将 orphan `running` / `waiting` 标记为失败。
2. Python 侧已新增 `Task`、`Harness`、`ReActHarness`、`PlanAndSaveHarness`、`HarnessFactory`、Skill / Tool 数据模型和内存型 `TaskManager`。这些是设计验证脚手架，尚未被 `backend.main`、HTTP API、事件总线或持久化任务表调用。

两者都不等同于最终 Python 后端内的线程级 task-agent：

- true resume 尚未实现。
- 结构化 artifact registry 仍需补强。
- MCP tool group 注入仍是目标形态。
- 复杂任务的 Plan-and-Execute 协议尚未完整落地。

迁移顺序固定为：先持久化 task-manager 的状态与事件，再接 deterministic
harness，随后迁移 CLI runner，最后才考虑 MCP、并发策略和 true resume。详见
[后端迁移路线](../backend-runtime-migration.md)。

## 职责边界

task-agent 负责：

1. 接收 task-manager 分配的任务 payload。
2. 根据任务类型执行 ReAct 或 Plan-and-Execute。
3. 调用被分配的工具、技能和外部 CLI / MCP 能力。
4. 持续向 task-manager 上报状态、进度、产物和错误。
5. 在需要用户补充信息或确认时进入等待状态。
6. 在完成、失败或被打断时返回结构化结果。
7. 生成可选的 `new_skill_candidate`，交由主 Agent / 用户确认。

task-agent 不负责：

1. 不直接向用户发消息。
2. 不直接维护前端 WebSocket。
3. 不直接读写长期记忆。
4. 不绕过 task-manager 操作任务状态。
5. 不自行扩大工具权限或工作区。
6. 不把内部 runner、payload、栈信息当作用户可见结果。

## task-manager 接口

task-agent 本身不暴露 HTTP 或 WebSocket。

前端只连接后端主进程的 HTTP API 和 WebSocket。任务创建、查询、打断、继续、中止、删除都通过后端主进程内的 task-manager 处理。

task-manager 提供给主 Agent 的能力：

- 创建任务。
- 查询任务。
- 打断任务。
- 继续任务。
- 重试任务。
- 修改任务。
- 中止任务。
- 删除任务记录。
- 打开或校验任务产物。

task-manager 提供给 task-agent 的能力：

- 读取控制信号。
- 上报任务事件。
- 登记输出产物。
- 进入等待用户输入状态。
- 结束任务并提交结果。

## 任务分类

### 短任务

短任务通常满足：

- 预计 3 分钟内完成。
- 需要 1-10 个工具步骤。
- 目标明确，不需要分阶段验收。
- 失败后可以直接向主 Agent 上报摘要。

例子：

- 读取一份文件并总结。
- 写一个小脚本。
- 下载一个文件并启动服务。
- 把一批图片转换格式。

### 复杂任务

复杂任务通常满足：

- 预计 1-30 分钟完成。
- 需要先理解上下文或代码库。
- 需要多个阶段或多个 artifact。
- 中途可能需要向用户确认方向。

例子：

- 给现有功能补一组测试。
- 实现一个软件小模块。
- 解读一个 codebase 并输出报告。
- 执行一次可回滚的小型重构。

### 暂不支持的超长目标

v0.2 不支持“让 Agent 自主工作数小时直到 token 耗尽”的超长目标模式。

如果用户提出超长目标，主 Agent 应把它拆成明确的复杂任务，或先询问用户确认阶段目标。

## 短任务执行模式

短任务使用 **ReAct 单循环**。

```text
[接收任务]
    ↓
[Reason] 判断下一步
    ↓
[Act] 调用工具
    ↓
[Observe] 读取结果
    ↓
[是否完成?] ──YES──→ [上报 completed]
    │
   NO
    ↓
[是否触发停止条件?] ──YES──→ [failed 或 waiting_for_info]
    │
   NO
    ↓
[继续下一轮 Reason]
```

默认约束：

| 约束项 | 默认值 | 说明 |
| --- | --- | --- |
| 最大工具调用次数 | 15 次 | 给 1-10 步任务保留少量重试余量 |
| 单步骤最大重试 | 2 次 | 同一步失败超过 2 次就等待用户或失败 |
| 最大执行时长 | 5 分钟 | 超时上报 failed |

短任务 system prompt 必须包含：

- 任务描述。
- 当前工作区。
- 输入附件 manifest。
- 可用工具和技能摘要。
- 风险限制和停止条件。
- 最终结果格式。

## 复杂任务执行模式

复杂任务使用 **Plan-and-Execute + checkpoint**。

```text
[接收任务]
    ↓
[PLANNING] 生成阶段计划
    ↓
[CHECKPOINT] 上报计划
    ↓
[执行阶段 1]
    ↓
[CHECKPOINT] 上报阶段结果 / artifact
    ↓
[执行阶段 N]
    ↓
[是否需要用户输入?] ──YES──→ [WAITING_FOR_INFO]
    │
   NO
    ↓
[完成或失败]
```

复杂任务计划应包含：

```json
{
  "task_id": "uuid",
  "task_type": "complex",
  "goal": "任务目标",
  "phases": [
    {
      "id": "phase-1",
      "title": "阶段标题",
      "expected_output": "阶段产物",
      "risk": "low | medium | high"
    }
  ],
  "needs_confirmation": false
}
```

复杂任务 checkpoint 必须上报：

- 当前阶段。
- 已完成动作摘要。
- 产物列表。
- 下一步计划。
- 是否需要用户确认。
- 是否建议继续、暂停或失败。

复杂任务仍然不能直接问用户。需要用户输入时，进入 `WAITING_FOR_INFO`，由 task-manager 把问题交给主 Agent。

## 状态机

```text
PENDING
  │
  ▼
PLANNING ────────────────┐
  │                      │
  ▼                      │
RUNNING ──→ CHECKPOINTING ┘
  │
  ├─ 需要用户信息 / 确认 ─→ WAITING_FOR_INFO ─→ RUNNING
  │
  ├─ 控制信号 interrupt ─→ INTERRUPTED
  │
  ├─ 达到限制 / 不可恢复错误 ─→ FAILED
  │
  └─ 任务完成 ─→ COMPLETED
```

状态定义：

| 状态                 | 含义                         |
| ------------------ | -------------------------- |
| `PENDING`          | task-manager 已创建任务，但尚未启动执行 |
| `PLANNING`         | 复杂任务正在生成计划                 |
| `RUNNING`          | 正在执行工具、技能、CLI 或 MCP 调用     |
| `CHECKPOINTING`    | 正在整理阶段进度或产物                |
| `WAITING_FOR_INFO` | 暂停执行，等待主 Agent 向用户取得信息     |
| `COMPLETED`        | 完成并提交结果                    |
| `FAILED`           | 失败并提交原因                    |
| `INTERRUPTED`      | 被用户或系统打断                   |

当前实现中的持久化状态可以先映射为：

- `queued` -> `PENDING`
- `running` -> `PLANNING` / `RUNNING` / `CHECKPOINTING`
- `waiting` -> `WAITING_FOR_INFO` 或等待外部子进程停止
- `completed` -> `COMPLETED`
- `failed` -> `FAILED`
- `interrupted` -> `INTERRUPTED`

## 任务 Payload

task-manager 创建 task-agent 时传入结构化 payload。

```json
{
  "task_id": "uuid",
  "profile_id": "default",
  "parent_session_id": "main-session",
  "task_type": "short | complex",
  "title": "任务标题",
  "description": "明确任务描述",
  "workspace": "/Users/xuan/Documents/AuraBot",
  "constraints": ["用户约束"],
  "expected_outputs": ["期望产物"],
  "input_artifacts": [
    {
      "id": "artifact_input_1",
      "artifact_type": "file | image",
      "original_name": "input.png",
      "mime_type": "image/png",
      "size_bytes": 12345,
      "uri": "受控本地路径"
    }
  ],
  "skill_groups": ["image"],
  "tool_groups": ["filesystem", "shell"],
  "sandbox": "read-only | workspace-write | danger-full-access",
  "approval_policy": "untrusted | on-request | never | on-failure",
  "timeout_ms": 300000
}
```

task-agent 只能使用 payload 中授权的工作区、工具组、技能组、输入附件和权限。

## 事件协议

task-agent 只通过 task-manager 上报事件。

### `task.agent.started`

```json
{
  "task_id": "uuid",
  "runner_id": "codex",
  "started_at": "2026-06-19T00:00:00+08:00"
}
```

### `task.agent.progress`

```json
{
  "task_id": "uuid",
  "summary": "正在读取输入文件",
  "phase_id": "phase-1",
  "step": 2,
  "total_steps": 5
}
```

### `task.agent.waiting_for_info`

```json
{
  "task_id": "uuid",
  "question_id": "task_question_uuid",
  "question": "需要用户确认的话",
  "reason": "ambiguous_goal | missing_tool | irreversible_action | repeated_failure",
  "options": ["可选项 A", "可选项 B"]
}
```

### `task.agent.artifact_created`

```json
{
  "task_id": "uuid",
  "artifact_id": "artifact_output_1",
  "artifact_type": "file",
  "uri": "受控本地路径",
  "name": "result.md",
  "mime_type": "text/markdown"
}
```

### `task.agent.completed`

```json
{
  "task_id": "uuid",
  "summary": "用户可读的完成摘要",
  "artifact_ids": ["artifact_output_1"],
  "steps_used": 7,
  "new_skill_candidate": null
}
```

### `task.agent.failed`

```json
{
  "task_id": "uuid",
  "summary": "用户可读的失败摘要",
  "reason": "timeout | tool_error | permission_denied | cancelled | unknown",
  "recoverable": true
}
```

### `task.agent.interrupted`

```json
{
  "task_id": "uuid",
  "summary": "任务已停止",
  "resume_supported": false
}
```

## WAITING_FOR_INFO

进入 `WAITING_FOR_INFO` 的情况：

1. 任务描述有多种合理解读。
2. 工具调用同一步连续失败 2 次。
3. 所需工具或技能不在已分配集合中。
4. 操作可能不可逆，例如删除、覆盖、批量移动文件。
5. 权限不足或 sandbox 不允许继续。
6. 复杂任务 checkpoint 后需要用户确认方向。

处理规则：

- task-agent 停止继续猜测。
- task-agent 上报 `task.agent.waiting_for_info`。
- task-manager 记录 pending question。
- 主 Agent 用自然语言问用户。
- 用户回答后，主 Agent 把答案交回 task-manager。
- task-manager 恢复 task-agent，或将任务重排为 queued 等待重新执行。

等待状态不能让任务线程无限占用资源。最小实现可以释放执行线程，只保留 pending question 和任务状态。

## Artifact 和附件协议

### 输入附件

- 用户提交任务时，输入附件先由后端复制到受控路径。
- task-manager 把附件 manifest 放入任务 payload。
- task-agent 只能读取 manifest 中列出的输入路径。
- 不允许根据用户自然语言猜测任意本地路径，除非用户明确授权并通过 sandbox 校验。

### 输出产物

- task-agent 生成文件后必须上报 `task.agent.artifact_created`。
- task-manager 把产物登记到 artifact registry。
- 完成摘要可以提到产物，但不能只靠自然语言路径作为唯一记录。
- 主聊天展示附件时优先读取 artifact registry。
- 打开文件前，backend 必须校验目标路径属于该任务登记产物。

### 兼容路径

当前过渡实现可以继续从 task event payload 推断产物路径，但只能作为兼容兜底。目标形态应以 artifact registry 为准。

## 安全边界

task-agent 启动前，task-manager 必须确定：

- sandbox。
- approval policy。
- trusted workspace。
- 可用 tool group。
- 可用 skill group。
- 输入附件受控路径。
- timeout。

安全规则：

1. 默认使用最小权限。
2. 未授权工具不可调用。
3. 未授权工作区不可读写。
4. 高风险或不可逆操作必须进入 `WAITING_FOR_INFO`。
5. task-agent 不自行提升 sandbox。
6. CLI runner 的 command、cwd、extra args 必须来自用户配置或安全默认值。
7. 子进程 stdout / stderr 进入内部事件，用户可见摘要由主 Agent 决定。

## 打断、取消、重试和恢复

### interrupt

用户要求“停一下”“先别做”时，主 Agent 调用 task-manager interrupt。

task-manager 写入控制状态，task-agent 在安全检查点读取并停止；CLI runner 可以取消 child process。

### cancel

取消表示任务不再继续执行，但任务记录可以保留。

### delete

删除表示移除任务记录或从用户界面隐藏。delete 不等于 interrupt；删除运行中任务前必须先停止执行。

### retry / rerun

重试表示基于原任务重新排队。复杂任务可以从最近 checkpoint 继续，当前最小实现可从头重跑。

### resume

true resume 是后续能力。当前最小实现不假装恢复执行上下文；应用重启时可以把 orphan `running` / `waiting` 标记为 `failed`，并给出可重试说明。

## Skill 候选

task-agent 可以在完成后返回 `new_skill_candidate`。

```json
{
  "name": "batch-image-convert",
  "description": "批量图片格式转换",
  "steps": "可复用步骤摘要",
  "tool_groups": ["filesystem", "shell"],
  "risk_notes": "需要写入输出目录"
}
```

规则：

- skill 候选只是建议。
- task-agent 不直接写入 skill registry。
- 主 Agent 决定是否提示用户保存。
- 用户确认后才由 skill-tool-registry 持久化。

## 与主 Agent 的边界

task-agent 的所有用户沟通都必须经过主 Agent。

主 Agent 负责：

- 把任务接收、进度、失败和完成转成 Aura 语气。
- 决定任务结果是否写入长期记忆。
- 选择是否保存 skill 候选。
- 处理任务向用户提出的问题。

task-agent 负责：

- 执行任务。
- 结构化上报事实。
- 避免暴露内部实现词给用户。

## 硬性约束

1. task-agent 是按任务创建的运行时执行者，不是常驻服务。
2. task-agent 不直接暴露 HTTP 或 WebSocket。
3. task-agent 不直接向用户发问。
4. task-agent 不直接读写长期记忆。
5. task-agent 不直接推送前端事件。
6. task-agent 只使用 task-manager 分配的工具、技能、工作区和附件。
7. 短任务使用 ReAct 单循环，复杂任务使用 Plan-and-Execute + checkpoint。
8. 高风险、不可逆、权限不足、工具缺失或目标歧义时必须进入 `WAITING_FOR_INFO`。
9. 输出产物必须登记 artifact，不只写在完成摘要里。
10. task-agent 失败不等于主 Agent 失败；失败应结构化回到主 Agent。
11. 当前最小实现可以失败式恢复 orphan 任务；true resume 不能在文档中假装已实现。
12. 用户可见话术由主 Agent 生成，不能暴露 runner、payload、event、栈信息或内部 task id。
