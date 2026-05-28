# 任务处理Agent


> 这个模块产生一个运行时Agent，负责处理聊天Agent给出的任务。
> Agent是运行时的，即用即销毁。并且是线程级，支持并发。

## Agent可以收到的任务分类

1. 短任务：帮用户执行1～10条指令即可。比如读一份文件，写一个脚本，或者下载一个软件，启动运行一个服务器。一般就是3分钟内完成的工作量。

2. 复杂任务：会涉及到规划-执行的流程，帮用户完成比较复杂的任务。比如测试一个代码的功能，实现一个软件的小模块，或者解读一片codebase。一般需要Agent工作1～30分钟。

```
# deleted for v0.2
3. 目标：超长任务，需要Agent自行决策完成一个长线任务，比如重构一个大型功能，或者直接开发一个完整功能。Agent甚至会直接工作数小时，知道Token额度耗尽或完工。
```

## 对外暴露的接口和webhook描述

### 接口

- 创建任务。需要做任务描述和指派工作空间。要确定是用哪种任务类型，以及工具和技能包。

- 打断任务。停止一个任务线程，等待主对话进一步指示。

- 查询任务。让主对话了解任务的进展。

- 继续任务。继续一个任务线程，甚至可以修改技能包和工具包以及任务目标。

### webhook

- 进一步询问信息。停下来问Chat Agent问题，或者认为工具包不够用，请求Chat Agent提供更多工具。

- Agent工作状态。返回一个枚举值，表示Agent当前的工作状态，有：思考中，工作中，困惑中，等待中，已完成，等待主对话进一步指示。

- 任务结束。结束后通知给主Agent进程，同时返回需要修改或者新增的skill。

## 短任务Agent


### 执行模式：ReAct 单循环

采用 **ReAct（Reason → Act → Observe）** 的简单单循环，无需提前生成计划。

```
[接收任务]
    ↓
[Reason] LLM分析下一步该做什么
    ↓
[Act]    调用工具
    ↓
[Observe] 观察工具返回结果
    ↓
[是否完成?] ──YES──→ [结束，上报结果]
    │
   NO
    ↓
[是否超出限制?] ──YES──→ [上报失败 / 触发 webhook 询问]
    │
   NO
    ↓
[继续下一轮 Reason]
```

---

### 硬性约束

| 约束项 | 默认值 | 说明 |
|--------|--------|------|
| 最大工具调用次数 | 15 次 | 留有重试余量（任务本身 1-10 步） |
| 最大重试次数（单步） | 2 次 | 同一步骤报错后最多重试 2 次，仍失败则触发询问 |

---

### 状态机

```
PENDING
  │
  ▼
RUNNING ──── 工具调用失败且超重试 ──→ WAITING_FOR_INFO
  │                                       │
  │ ◄─────────────────────────────────────┘
  │                                  chat-agent 回复后继续
  │
  ├──── 达到步骤/时间上限 ──→ FAILED
  │
  ├──── 主动打断指令 ──→ INTERRUPTED
  │
  └──── 任务目标完成 ──→ COMPLETED
```

---

### 执行流程细节

#### 1. 启动时接收的 Payload

```json
{
  "task_id": "uuid",
  "task_type": "short",
  "description": "帮我把桌面上的图片批量转成 jpg",
  "workspace": "/Users/xxx/Desktop",
  "tools": ["filesystem", "shell"],
  "skills": ["image-convert"]
}
```

#### 2. 上下文构建（System Prompt 要素）

- 任务描述（`description`）
- 已分配的工具列表和用法摘要（从 MCP 服务获取 tool schema）
- 已分配的技能内容（从 `skill-registry` 读取）
- 硬性约束提示：**你最多可以调用 15 次工具，请直接行动，不要做长篇计划**

#### 3. 循环中止条件（任一满足即停止）

- LLM 输出"DONE"信号（约定的终止词或 `finish_reason`）
- 工具调用次数 ≥ 15
- 执行时长 ≥ 5 分钟
- 收到外部 `interrupt` 信号

---

### 询问主Agent的时机（触发 WAITING_FOR_INFO webhook）

以下情况应立即暂停并向 chat-agent 询问，而非继续猜测：

1. 单步骤工具调用连续失败 2 次（例如文件路径不存在、命令报错）
2. 任务描述存在歧义，执行前发现有多种合理解读
3. 发现任务可能造成**不可逆操作**（如删除文件、覆盖数据），需要用户确认
4. 所需工具不在已分配的工具组中

---

### 完成后的上报

任务结束（COMPLETED / FAILED / INTERRUPTED）后，向 chat-agent 上报：

```json
{
  "task_id": "uuid",
  "status": "COMPLETED",
  "summary": "已将桌面 12 张 png/webp 图片转换为 jpg，保存至原路径。",
  "artifacts": ["path/to/result"],
  "steps_used": 7,
  "new_skill_candidate": {
    "name": "batch-image-convert",
    "description": "批量图片格式转换",
    "steps": "..."
  }
}
```

- `new_skill_candidate` 为可选字段：若本次执行中形成了可复用的步骤序列，由 LLM 提炼后附带上报，由 chat-agent / 用户决定是否写入 `skill-registry`。