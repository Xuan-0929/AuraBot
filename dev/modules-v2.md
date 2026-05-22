
> 此为第一轮产品设计归档前的终稿。

# 后端模块划分

后端采用微服务。每个模块运行一个独立 Python 进程，监听一个固定端口。

进程间通信采用http+websocket。

---

## gateway

路由层
- 前端通过gateway与后端各个微服务模块通信。
- 支持http请求转发和websocket通信。

唤醒层
- 定时任务或者计时任务启动
- 控制ChatAgent主动唤醒

---

## connector

搜集和发送用户的信息。

链路图
- `桌面端 -> gateway -> connector -> guard-agent -> chat-agent -> connector`
- `QQ/微信/飞书 -> connector -> guard-agent -> chat-agent -> connector`
最后，由clientagent把AI的对话推给用户。

---

### `guard-agent`

对碎片化消息进行“peek”。
判断状态为以下一种：
1. 用户正在说，还没说完
2. 完全的STT转录杂音产生的垃圾
3. 用户说完了，可以开始回复了

并在此处做处理。
情况1:启动时钟，30秒内有新的内容送入就认为用户还没说完，重新peek。如果30秒内没有新内容，检测是否是一个新的端点并submit给chat。
情况2:忽视
情况3:如果30秒内没有新内容，检测是否是一个新的端点并submit给chat。

---


## `chat-agent`

对应 PRD"主对话Agent"：全生命周期单进程、单 session、串行处理。

职责：
- 维护一个全局消息队列，依次处理来自各渠道（QQ/微信/前端/定时任务）的触发
- 持有唯一的长期 session 上下文，伴随应用生命周期
- 管理短期记忆
- 构造带 SOUL 语气的回复，断句后推送给对应渠道
- 输出情绪和动作控制信号，由 `frontend-api` 转发给前端驱动 Live2D
- 读写 `memory-service`（仅此模块有读写权限）
- 向 `task-agent` 派发任务请求，可查询进度、发出打断指令

---

##  `task-agent`

这个进程会显式创建多个线程，每个线程处理一个任务。

创建即用即销毁的运行时任务agent。

任务agent生命周期：
1. 接收 `chat-agent` 下发的任务（携带分配的技能组和工具组）
2. 每个任务作为独立子线程运行，支持多任务并发
3. 暴露进度查询接口和打断接口
4. 任务完成后将结果上报 `chat-agent`；执行中途需要信息时，向 `chat-agent` 发问
5. 任务完成后，选择性将本次任务中提炼出的可复用步骤写入他的skill组`skill-registry`。

---

## memory-service

提供长期记忆的读写接口。

---

## voice-service

职责：
- **STT**：接收音频流，转写为文字，返回给 `frontend-api` 或直接触发 `chat-agent`
- **TTS**：接收文字，合成音频；支持流式断句——遇到句末标点（。？！…）即发起合成，降低端到端延迟
- 作为 STT → LLM → TTS 流水线的语音端点

---

## MCP服务器

专门为Agent提供工具调用的处理服务。

支持用户可插拔地配置分级工具组，注册进MCP协议。
