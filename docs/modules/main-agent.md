# Main Agent V1 历史设计

> 历史参考：本文保留前端统一接入之前的 Runtime 内部设计，不再作为当前产品入口、配置或线协议事实源。2026-09-05 起，Console 文字和语音统一经过 Pet 的唯一 WebSocket；默认文字，语音延迟初始化；人格归 Electron settings.json 的保存/应用；桌面交付历史独立存储；Memory 外部 API/UI 移除。当前规范见 [产品需求](../PRD-v2.md)、[系统模块](../modules-v2.md)、[后端契约](../backend-runtime-contract.md) 和 [配置与生命周期](../configuration-and-backend-lifecycle.md)。下文“仅语音”“不支持文本 submit”“Profile SQLite 独占人格”等旧约束已失效。

## 1. 产品范围

Main Agent 是 AuraBot Pet 的常驻语音对话控制器。当前产品只有一个 Pet Renderer、一个全局
Active Profile、一个该 Profile 的 Runtime，以及共享 TaskManager。旧 Console 文本聊天保留
原链路，不接入本协议。

~~~mermaid
flowchart LR
  PET["Pet Renderer"] <--> HUB["RealtimeHub"]
  HUB <--> SPEECH["SpeechService<br/>STT · VAD · EOU · TTS"]
  SPEECH <--> RT["MainAgentRuntime"]
  RT --> MODEL["Chat Model"]
  RT --> CTX["context.sqlite3"]
  RT --> MEMORY["MemoryService<br/>memory.sqlite3 · Qdrant"]
  RT --> TASK["共享 TaskManager"]
  RT --> TOOLS["Tavily · Workspace"]
~~~

当前不实现：

- 多 Renderer、多 Active Profile 或跨进程 Runtime。
- Live2D motion/expression capability；协议和工具 schema 中也没有预留 token。
- realtime 诊断页、故障注入、文本 submit 或旧 wire compatibility。
- Backend Profile 创建、人格文件或 Profile 切换 UI。

## 2. Profile 与生命周期

Electron Profile SQLite 是 Profile 和结构化 Soul 的唯一事实源。Pet 开启麦克风时把 active
Profile 的身份和格式化 Soul 发送给 Backend：

~~~http
POST /api/v1/main-agent/realtime-token
Origin: http://127.0.0.1:6400
Content-Type: application/json

{
  "profileId": "profile_default",
  "soul": "已经格式化的有效 Soul system prompt"
}
~~~

`profileId` 只允许字母、数字、`_`、`-`，最长 128 字符；`soul` 必须非空且不超过
32 KiB。Electron 的 `buildSoulPrompt` 同时供旧 Console Chat 和 token 请求使用，Backend
不再保存 `profile.yaml` 或 `SOUL.md`。

Backend 启动时不创建 Runtime、Speech、Context、Memory 或 Qdrant。第一次合法 token 请求才
激活 Profile：

~~~mermaid
sequenceDiagram
  participant P as Pet
  participant H as RealtimeHub
  participant A as ActiveProfileManager
  participant R as Profile Resources
  P->>H: token(profileId, soul)
  H->>A: activate(profileId, soul)
  alt 同一 Profile
    A->>R: update_soul(soul)
    A->>H: replace voice generation
  else 不同 Profile
    A->>H: close old voice session
    A->>R: shutdown Runtime → Speech → Memory → stores
    A->>R: open new Profile resources
    A->>H: set active generation
  end
  H-->>P: one-shot token
~~~

同一 Profile 的新 token 保留 Runtime/Context/Memory，只更新下一轮 invoke 的 Soul，并替换旧 voice
session。不同 Profile 串行切换；任何时刻最多一个 active Profile。Profile 运行目录只有：

~~~text
profiles/<profile_id>/
├── context.sqlite3
├── memory.sqlite3
└── qdrant/
~~~

本版本不迁移旧 Main Agent 数据。开发环境升级后删除旧 Profile 运行目录再重建。

## 3. 输入、调度与授权

### 3.1 ActiveInput

每次新 STT session 生成稳定 `segment_id`。同一 utterance 的 partial/stable/final 更新覆盖该
segment 第一次出现的位置；用户在 durable external commit 前继续说出的新 utterance 追加到同一
ActiveInput。ActiveInput 保留：

- `input_id`
- 单调递增 `revision`
- 有序 `segments`
- 由 segments 合成的 `latest_transcript`，仅用于 PREPARE admission、EOU 与 recall query

Context 写入 `user_segment {segment_id,input_id,text}`。Fold 后唯一对话表示是
`conversation_turns`；没有 `transcript` 或 `delivered_clauses` 兼容副本。Production speech coordinator
会让每个非空 normalized transcript update 请求 PREPARE admission；即使 火山引擎 partial 的
`stable_prefix` 为空也一样，实际 admission、合并和每 input 次数上限仍完全由 Runtime 现有策略决定。

### 3.2 Invoke 调度

控制 invoke 全局 single-flight，等待期间只保留一个 pending trigger：

~~~mermaid
flowchart LR
  PREPARE -->|被更高优先级覆盖| CONTINUE
  CONTINUE -->|被更高优先级覆盖| RESPOND
  RESPOND --> RUN["下一个 control invoke"]
~~~

优先级固定为 `RESPOND > CONTINUE > PREPARE`。PREPARE 仍受增长、间隔、标点、最大等待和
每 input 上限约束；EOU incomplete 可请求高优先级 PREPARE，但不会创建回复授权。

StrongPause 在创建 RESPOND 前等待旧 PREPARE 完整 settlement。Provider 阻塞在下一个 async
chunk 时，Harness 同时等待取消 Event；barge-in 或 StrongPause 撤销后会关闭 provider stream，
不会无限等待模型再发一个 chunk。

### 3.3 ResponseGrant 与 EffectFence

RESPOND 创建绑定 `input_id + revision + context_version` 的 ResponseGrant；CONTINUE 只能复用当前
有效 grant，不能自行创建。每个 SAY 或 durable action 执行前重新校验：

- 当前 invoke、input、revision、context version 未变化。
- grant 仍是当前 grant。
- 没有本地 speech candidate 或后端 confirmed speech。

这使晚到的 SAY、Task action 或旧 Observation continuation fail closed。

## 4. 模型与工具

Provider 固定为 async OpenAI-compatible Chat Completions SSE。每次请求只有三条消息：

1. 固定 `MAIN_AGENT_CONTROL_PROMPT` system。
2. Electron 提供的有效 Soul system。
3. 不可变 Snapshot JSON user。

Main Agent 自行判断是否有话需要对用户说。需要说话时，必须将 `say_to_user` 作为该次 invoke 的第一个工具调用（index 0），供流水线提前解析与交付；不需要说话时可以只调用其他工具，也可以不调用任何工具。
没有调用 `say_to_user` 就表示模型选择静默，不是协议故障。普通 assistant content 仍被丢弃，既不转成用户消息，也不因此向前端显示 debug 错误。

排查采用后端已有的只读 `/api/v1/metrics`：`main.invoke.silent` 统计未调用 `say_to_user` 的正常完成调用，`main.receipt.*` 统计工具接收、校验拒绝及普通 content 丢弃等回执。计数有界，不保存提示词、回复正文或密钥，不添加模型重试或后台轮询。真实模型请求/传输失败仍走已有的产品故障提示。
Snapshot 用一次 JSON round-trip 复制，12,000 字符预算优先保留 runtime state 和 tool schemas，
再从最旧 conversation、memory、observation 开始裁剪。

ToolSurface 是冻结的 `ToolDefinition` tuple。规则：

- PREPARE：safe read，加最多一条 think note；无 SAY 或 durable action。
- RESPOND/CONTINUE：有效 grant 下允许 SAY 和 durable action。
- 一个 invoke 不混合 safe read 与 durable action。
- SAY 优先执行。
- safe read 使用全局 `asyncio.Semaphore(3)`，detach 后由 Runtime 的 ObservationLedger 做去重、
  terminal persistence 与 CONTINUE trigger。

`say_to_user` schema 只有：

~~~json
{
  "type": "object",
  "properties": {
    "text": {"type": "string", "minLength": 1, "maxLength": 8000}
  },
  "required": ["text"],
  "additionalProperties": false
}
~~~

增量 parser 只支持这一个合法顶层形态，同时保留 escape、Unicode surrogate、控制字符、duplicate
key、chunk boundary 和最终 `json.loads` 校验。其他工具等待完整 arguments 后解析。

工具只在真实 executor 存在时暴露：

- 配置 Tavily key：`web_search` 调 `/search`，opaque source ID 保存在有限 map；`web_read` 只接受
  该 map 的 ID，并用 `/extract` 获取 markdown，结果受 64 KiB Observation 上限约束。
- 未配置 Tavily key：两个 Web 工具均不存在。
- Workspace 工具只暴露配置中的 workspace ID，并保留 no-follow 与 secret-path 边界。
- Task 工具通过 TaskBridge fresh-read owner/version；三秒 proposal 在 TaskManager 接受前可被
  barge-in 或 Profile switch 取消，接受后才成为 durable fact。

## 2026-09-06 内部实现收敛

TaskOperations 接管三秒提案、冻结恢复、已接管控制与 Task 事实同步；Runtime 保留输入/grant
和声学产品语义。Task 最新事实进入 Context 专表，版本去重不做完整 fold。启动先完成当前
记录对齐再接收工作；投影失败报告一次，无无限重试。旧 ShortTerm 实现和兼容 façade 已删除。

Speech 由 ProfileResources 唯一持有，Hub 不双写。文字打断取消旧 TTS、reset 当前采集，
保留语音模式和 worker。reset 废弃旧帧/旧 sink，并结清推理线程后复用 detector。静态许可证
不再逐帧读取。EOU 时限、VAD 候选暂停/误报恢复/确认取消、PREPARE/RESPOND/CONTINUE、
StrongPause 和 ACK 交付规则均保留。`PendingTrigger.min_context_version` 未被消费，已删除。

## 5. 极简 realtime 协议

subprotocol 仍为首个未发布的 `aurabot.main-agent.v1`，不兼容旧 wire。

Client → Server：

- binary：固定 640 bytes，PCM16LE mono 16 kHz，即 320 samples / 20 ms。
- `renderer.ack {clause_id, complete}`。

Server → Client：

- `session.ready`
- `playback.pause` / `playback.resume`
- `clause.offer {clause_id,text}`
- binary：当前唯一 clause 的 PCM16LE TTS chunks
- `clause.audio_end {clause_id}`
- `clause.cancel {clause_id,reason}`
- `session.error {code}`

WebSocket/TCP 已提供可靠有序传输，所以没有 header、seq、command ID、stream ID、sample coordinate、
renderer epoch 或 mode。WebSocket 生命周期就是 mic session；没有 `session.open`、`mic.start`、
`mic.stop`、`text.submit`、手工 speech 命令或自动重连。

~~~mermaid
sequenceDiagram
  participant P as Pet
  participant H as VoiceSession
  participant S as SpeechService
  participant R as Runtime
  participant W as Playback Worklet
  P->>H: token-bound WebSocket
  H-->>P: session.ready
  P->>H: raw mic PCM
  H->>S: bounded queue (50 frames)
  S->>R: transcript / pause decisions
  R->>S: speech clause
  S->>H: clause.offer
  H-->>P: clause.offer + raw TTS PCM + audio_end
  P->>W: append PCM; mark final
  W-->>P: physical cursor complete
  P->>H: renderer.ack complete=true
  H->>R: commit delivered clause
~~~

Mic queue 满时丢最旧 frame，不发送诊断事件。服务器发送只有一个短 async lock。Renderer replacement
依靠 session 对象身份；stale producer 被拒绝。

## 6. Speech 与 barge-in

SpeechService 只拥有 SpeechInputCoordinator、ClauseSpeechDelivery 和一个 mic consumer task；它不
关闭 Runtime。speech 未配置或资产/依赖不可用时，不创建 disabled object，token 请求返回 unavailable。

STT、VAD、EOU 和 Runtime port 只支持生产 async 契约。火山引擎 TTS 使用 HTTP 流式输出，transport 将 PCM 传入既有 callback spool：

- 每 clause 独立 transport/cancel handle。
- HTTP 阻塞读取在 worker thread；ASR 使用 WebSocket 双向流。
- callback 写入上限 120 秒 PCM 的临时文件，消费端每次读取最多 200 ms 音频。
- 第一块 callback 数据立即 yield。
- overflow、error、cancel、complete 只收敛一次。

Pet 的一个 AudioContext 同时承载 mic capture 与 TTS playback。RMS gate 参数：

- noise floor EMA：初始 `0.005`，非 candidate 时 `alpha=0.05`。
- attack：`max(0.020, noiseFloor × 3.0)`，一个 20 ms frame 即暂停。
- release：`max(0.012, noiseFloor × 1.8)`，连续 10 frames（200 ms）解除。

`localPaused || serverPaused` 时 Worklet 停止播放游标但保留 PCM。local release 只有在 server 未阻塞
时续播；后端 confirmed speech 发 `clause.cancel` 才 flush。播放队列用游标索引消费 chunk，不逐
sample 移动数组。

Speech Ledger 只按真实物理阶段转换：

~~~mermaid
stateDiagram-v2
  [*] --> DRAFT: Runtime accepts SAY
  DRAFT --> TTS_QUEUED: bounded delivery queue accepts
  TTS_QUEUED --> PLAYING: offer + first PCM reaches current session
  PLAYING --> DELIVERED: Worklet cursor + complete ACK
  DRAFT --> FAILED: queue/initialization failure
  TTS_QUEUED --> FAILED: TTS/Renderer failure
  TTS_QUEUED --> INTERRUPTED: cancel/replacement/shutdown
  PLAYING --> FAILED: transport failure
  PLAYING --> INTERRUPTED: partial ACK/barge-in/replacement
~~~

只有 PLAYING 的完整 ACK 才追加 assistant conversation turn。未播放、部分播放、failed 或 interrupted
文本永远不进入 Canonical Context。

## 7. Profile Memory

Main Agent 不判断一段对话是否值得记忆，也不拥有第二套 episode、持久化 job 或重试队列。
`ContextStore` 是唯一的未归档 conversation buffer；既有 `MemoryService` 独占提炼内容、长期保存和
召回策略。Runtime 只保留一个 idle timer 和一个 sealed archive worker 作为临时编排 owner。

`ContextStore.conversation_batch()` 只提交两类 canonical turn：stable/final user segment，以及
Renderer 完整 ACK 后落入 Context 的 assistant clause。中间 partial 快照不会形成独立 turn；未完整
播放的回复、Observation、Task fact 和 think note 都不进入 Memory 提炼输入。同一 user segment 的
多次更新按现有 last-write-wins 规则折叠。

~~~mermaid
flowchart LR
  Collecting --> Timer["idle timer"]
  Timer -->|新文本 / VAD / STT 重置| Timer
  Timer -->|300 秒到期| Sealed["seal boundary + retire old input"]
  Sealed --> Worker["single archive worker"]
  Worker -->|失败，保留 pending/raw| Retry["idle retry timer"]
  Retry -->|同一 source_id| Worker
  Worker -->|成功| Commit["Memory idempotency + Context atomic commit"]
  Sealed -.新输入创建新 causal scope.-> NewInput["new ActiveInput"]
  Worker -.不阻塞、不取消.-> NewInput
~~~

Runtime 在每次 PREPARE、RESPOND、CONTINUE snapshot 构建时直接调用当前 Profile 的
`MemoryService.recall`，最多请求 6 条、2,400 字符；召回失败按空记忆 fail-soft，不影响回答。

用户确认输入结束后，Runtime 持久化最后活动时间并启动 300 秒 idle timer。新文本、VAD start 或
有效 STT 只取消或重置 timer；噪声 VAD 被拒绝后按原 deadline 恢复。到期后 Runtime 先等待 control
invoke 与 Speech Ledger 收敛，再在锁内冻结 singleton pending boundary，并立即退休旧 ActiveInput、
grant 和 trigger。seal 后的唯一 archive worker 从 canonical `context_events` 重构该边界 turns，再调用
一次 `MemoryService.remember_conversation`；新输入不会等待或取消这个 worker，而是直接创建新的
causal scope。

失败会保留 pending 和全部 raw Context，下一个 idle deadline 只重试同一 `source_id`；零条长期记忆
也属于成功。Memory 成功后，Runtime 在锁内用一个 Context SQLite transaction 写入稳定摘要、删除
`through_sequence` 以内的 conversation 与 terminal Speech Ledger row，并删除 pending。边界后的新
input、grant、trigger、conversation 和非 conversation 事件不受影响；如果仍有后续 raw conversation，
会按其持久化活动时间恢复剩余 deadline。

批次 `source_id` 由 Context 事件边界稳定生成。MemoryService 对同一
`profile_id + main_agent_conversation + source_id` 幂等，因此 Memory 已写入但 Context 尚未提交时，
重启重试不会再次 extraction 或重复创建长期记忆。Profile 切换和 Backend shutdown 会取消 timer 与
archive worker，但不提交或清理 pending/raw；重新激活时从 `context.sqlite3` 恢复并重试。

Main Agent 不再暴露 Profile 专用 Memory browse/forget/retry HTTP 路由。每个 Profile 仍以独立目录
隔离 `context.sqlite3`、`memory.sqlite3` 和 `qdrant/`，同时所有 Memory 调用都携带 `profile_id`，
形成物理目录与查询条件的双重隔离。

## 8. 安全边界与验收

- token 绑定标准化 Origin、Profile ID、generation，15 秒有效且一次性消费。
- Backend 不允许 `null`、`file://` 或任意 Origin。
- Electron 只为 Pet window 的 `webContentsId`，且只对 loopback Gateway 请求规范化 Origin；Console、
  Settings、外部页面不获得该能力。
- `say_to_user` 是唯一用户可见语言出口。
- Workspace no-follow、Tavily opaque source ID、Task owner/version 和 EffectFence 均 fail closed。

关键回归命令：

~~~bash
backend/.venv/bin/python -m unittest discover -s backend/tests -t .
cd frontend
npm test
npm run typecheck
npm run build
npm run test:e2e
~~~
