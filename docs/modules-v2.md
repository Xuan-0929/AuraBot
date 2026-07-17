# 后端模块划分

> 这是 AuraBot 的目标运行时架构。当前迁移状态见
> [当前状态](aurabot-development-status.md)，实施顺序见
> [后端迁移路线](backend-runtime-migration.md)。

后端采用 **单服务进程架构**。

默认运行时不再把每个模块拆成独立 Python 进程，而是启动一个后端主进程，由该进程承载 HTTP API、WebSocket、主对话、门控、记忆、任务管理、定时唤醒、配置管理等核心业务。

前端与后端之间采用 HTTP + WebSocket；后端内部模块之间采用 Python 方法调用、事件总线、队列和后台任务协作，不通过本机 HTTP 二次转发。

## 当前实现与迁移边界

当前 Electron TypeScript core 仍是聊天、记忆和任务写链路的事实来源；Python
FastAPI 网关已具备配置加载和只读数据接口，Python memory service 与 Agent
脚手架尚未成为请求路径的一部分。迁移期间：

1. 新 Python 功能只经后端内部方法调用，不在模块间二次 HTTP 转发。
2. 每次只迁移一条完整垂直链路，并明确唯一写入 owner。
3. Electron 只通过 HTTP / WebSocket 使用已迁移能力；未迁移能力可暂时保留现有 IPC。
4. connector、MCP、重型语音 worker 与复杂任务 resume 不属于主链路第一阶段。

---

## 运行进程

### 1. Electron + Vue + TypeScript 前端进程

职责：
- 渲染桌面端 UI、控制台、Live2D 桌宠、设置页和任务状态页
- 通过 HTTP 调用后端接口
- 通过 WebSocket 接收后端实时事件
- 不承载核心业务逻辑

前端中的 TypeScript 只负责界面状态、交互和网络通信。主对话、记忆、任务、门控、工具调用等业务全部交给 Python 后端。

---

### 2. 后端主进程

一个常驻 Python 进程，负责 AuraBot 的核心运行时。

职责：
- 暴露 HTTP API
- 管理前端 WebSocket 连接
- 接收桌面端、QQ、微信、飞书等渠道输入
- 执行门控判断
- 维护主对话 Agent 队列
- 管理短期记忆和长期记忆
- 管理定时任务和主动唤醒
- 管理任务 Agent 生命周期
- 管理 Skill / Tool 配置
- 向前端推送聊天、任务、Live2D、提醒等实时事件

该进程是默认唯一的后端常驻进程。

---

### 3. MCP 服务器

MCP 服务器是外部工具能力进程，不属于后端主进程内部模块。

职责：
- 为 Agent 提供工具调用能力
- 支持用户可插拔地配置工具组
- 暴露 tool schema 和 tool call 接口

后端主进程负责读取用户配置、连接或启动 MCP 服务器，并在创建 runtime task agent 时把可用工具组分配给任务 Agent。

---

### 4. Runtime Task Agent 线程

任务 Agent 是即用即销毁的线程级运行时 Agent。

它不是默认常驻服务，也不由单独的 task-agent 进程管理。只有用户创建任务时，后端主进程才在自身进程内创建对应的 runtime task agent 线程；任务完成、失败或被中止后，该线程退出，运行时销毁。

Runtime task agent 的管理原则：
- task-manager 运行于后端主进程内
- 每个任务对应一个后端主进程内的工作线程
- 任务线程通过 task-manager 上报状态、进度、询问和最终结果
- websocket-hub 仍由后端主进程统一持有，任务线程不直接维护前端 WebSocket
- 任务线程可以调用 MCP 服务器或外部工具进程，但不另起一个常驻 task-agent 管理进程

这样可以保持 WebSocket 链路简单：前端只连接后端主进程，任务进度由后端主进程统一推送。

---

# 后端主进程内部模块

以下模块是后端主进程内的代码模块，不是独立服务进程。

---

## api-layer

前端接口层。

职责：
- 提供 HTTP API
- 提供 WebSocket endpoint
- 做请求鉴权、参数校验、错误格式化
- 把请求转交给后端内部业务模块

说明：
- 前端只知道后端主进程的 API 和 WebSocket 地址
- 后端内部模块之间不通过 API 层互相转发

---

## websocket-hub

实时事件层。

职责：
- 管理控制台、桌宠、设置页等前端 WebSocket 连接
- 推送主对话回复增量
- 推送任务状态变化
- 推送 Live2D 情绪和动作信号
- 推送主动唤醒和提醒事件

典型事件：
- `chat.message.created`
- `chat.delta`
- `chat.message.completed`
- `agent.status.changed`
- `live2d.motion`
- `task.created`
- `task.progress`
- `task.waiting_for_input`
- `task.completed`
- `task.failed`
- `notification.push`

---

## connector-hub

统一接入层。

职责：
- 收集来自桌面端、QQ、微信、飞书等渠道的用户输入
- 将不同渠道输入统一转换为内部消息格式
- 记录上一次用户交互渠道，用于主动搭话时选择推送目标
- 将后端回复发送回对应渠道

链路图：
- `桌面端 -> 后端主进程 -> connector-hub -> guard-agent -> chat-agent -> connector-hub -> 桌面端`
- `QQ/微信/飞书 -> connector-hub -> guard-agent -> chat-agent -> connector-hub -> QQ/微信/飞书`

QQ、微信、飞书连接器如果依赖复杂或需要独立保活，可以作为可选外部进程存在；但 connector-hub 本身属于后端主进程内部模块。

---

## guard-agent

门控 Agent。

职责：
- 对碎片化消息进行 peek
- 判断用户是否还在输入
- 过滤 STT 转录杂音
- 判断消息是否已经可以提交给主对话
- 检测话题切换，并在需要时为上一段话题打断点

判断状态：
1. 用户正在说，还没说完
2. 完全的 STT 转录杂音产生的垃圾
3. 用户说完了，可以开始回复

处理策略：
- 情况 1：启动等待时钟，等待窗口内有新内容则继续合并并重新 peek
- 情况 2：忽略
- 情况 3：提交给 chat-agent

桌面端控制台和明确的文字输入可以跳过碎片门控，直接提交主对话。

---

## chat-agent

主对话 Agent。

对应 PRD 中的“主对话 Agent”：全生命周期、单 session、串行处理。

职责：
- 维护全局消息队列，依次处理来自各渠道的触发
- 持有唯一长期 session 上下文，伴随应用生命周期
- 管理短期记忆
- 读写长期记忆
- 构造带 SOUL 语气的回复
- 对回复断句，并通过 connector-hub 推送到对应渠道
- 输出情绪和动作控制信号，由 websocket-hub 推给前端驱动 Live2D
- 向 task-manager 派发任务请求
- 查询任务进度、打断任务、继续任务

约束：
- 主对话 Agent 不并发处理用户输入
- 所有渠道触发都进入同一个队列
- 用户感知上始终只有一个主 session

---

## task-manager

任务管理模块。

职责：
- 接收 chat-agent 下发的任务
- 在后端主进程内创建 runtime task agent 线程
- 为任务分配技能组和工具组
- 维护任务状态
- 暴露任务查询、打断、继续、中止能力
- 接收 task agent 的进度、询问和最终结果
- 将任务结果交回 chat-agent

Runtime task agent 生命周期：
1. 接收任务描述、工作区、技能组和工具组
2. 在后端主进程内创建任务线程
3. 执行任务并持续上报进度
4. 如需用户补充信息，暂停并向 chat-agent 发问
5. 任务完成后返回结果和可选 skill 候选
6. 任务线程退出，运行时销毁

---

## memory

记忆模块。

职责：
- 维护主对话短期记忆
- 持久化短期记忆断点
- 写入长期记忆
- 查询长期记忆
- 处理冲突记忆和选择性遗忘

约束：
- 记忆模块不作为独立服务进程
- 只有 chat-agent 可以直接读写记忆

---

## scheduler

定时和主动唤醒模块。

职责：
- 管理 cron 任务
- 管理倒计时任务
- 触发主动搭话
- 将到期事件送入 chat-agent 队列

触发来源：
- 固定时间任务
- 倒计时任务
- 任务 Agent 返回结果
- 任务 Agent 向用户发问
- 用户设置的随机主动事件

---

## voice

语音模块。

职责：
- STT：接收音频或音频流，转写为文字
- TTS：接收文本，合成语音
- 支持流式断句：遇到句末标点后即可发起下一阶段处理，降低端到端延迟

说明：
- 默认作为后端主进程内部模块
- 如果接入本地重型 STT/TTS 引擎，可以将具体语音引擎作为可选外部 worker 进程

---

## skill-tool-registry

技能和工具注册模块。

职责：
- 管理 Skill 组
- 管理 Tool 组
- 读取用户 MCP 配置
- 为 task-manager 提供可分配的技能组和工具组
- 接收 task agent 提炼出的可复用 skill 候选

Skill 和 Tool 仍以二级目录组织：
- skill group -> skills
- tool group -> tools

---
