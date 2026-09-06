# Backend Runtime Contract

本契约描述当前 Electron/Pet 与 Backend 边界。配置及进程控制见 [配置与生命周期](configuration-and-backend-lifecycle.md)。

## HTTP 与进程身份

| 接口 | 用途 |
| --- | --- |
| `GET /health` | 精确返回 `{"ok":true,"service":"backend"}`。 |
| `GET /api/v1/readiness` | `ready`、`configRevision`、`processId`、`instanceId`、`capabilities` 与本地组件状态。 |
| `POST /api/v1/restart/prepare` | `{force:boolean}` → `{ready,busy:{tasks,memoryWriting,mainAgent}}`。 |
| `POST /api/v1/main-agent/realtime-token` | `{profileId,soul}` → 一次性 capability。 |
| `WS /api/v1/main-agent/realtime` | 唯一文字/语音会话，subprotocol `aurabot.main-agent.v1`。 |
| `GET /api/v1/task-events?ownerProfileId=…` | 必填 Profile；只读 `task.snapshot` SSE，最近 100 项标题/状态。 |
| `GET /api/v1/metrics` | 有界计数/计时，不含对话内容。 |

Token 和 restart prepare 使用受限 Origin。Electron 用启动时的实例标识与配置 revision 确认新进程就绪，不能把其他服务的健康响应误认为自己的进程。Readiness 不执行模型、embedding、Qdrant 网络或付费探测。

非强制重启遇忙返回 `ready:false`，不得改变状态。允许重启时，在 admission 锁内阻止新 token、Runtime 输入/效果和 Task 变更/派发，再由 Electron 请求退出。Memory idle timer 不等于实际写入。

Task 创建、查询、回答、暂停、恢复、取消、删除、重跑均由 Main Agent 进程内调用 TaskManager，见 [Task Runtime](modules/task-agent.md)。旧 Task REST 与 DTO 已删除。桌面只消费标题/状态投影；Memory 外部路由与管理 UI 已删除。旧 `/task`、Plan、MCP、chat/debug/registry 兼容 API 不恢复。

## Task 状态流

每次连接先发 `event: task.snapshot`，`data: {"tasks":[{"id":"…","title":"…","status":"…"}]}`。
只查询该 Profile 最近创建且未删除的 100 项，降序。创建、状态变化、删除会唤醒查询；执行输出
不会唤醒快照。重复快照不发送，空闲使用 SSE keepalive。重连重新发送当前快照，无事件 ID、
分页、Last-Event-ID 回放或执行正文。Electron 每个可见控制台只保持一个可取消订阅。

Main Agent 启动先取内部事件 watermark，分批对齐当前 Profile 的记录（含 tombstone），然后
监听 watermark 后的重要事件。Context 通过 `upsert_task_fact` 原子去重，不再全量 fold 查版本。
已提交的操作先标记因果归属再写投影；普通重复事实不改变当前输入。投影失败只报告一次，
Task 持久事实保留，下次正常启动对齐，不无限重试或自动重跑任务。

## Profile 与资源

Backend startup 创建 TaskManager、RealtimeHub 和 ActiveProfileManager；合法 token 才打开一个 Profile 的 Context、Memory 和文字 Runtime。Inactive Profile 不保持 timer、SQLite/Qdrant 连接或 worker。

Profile 身份由 Electron 提供；人格由已应用 JSON 的 `persona` 投影，经 Pet 格式化为 `soul`。Backend 不保存 Profile 配置或 `SOUL.md`。会话默认文字，`session.ready` 不开启麦克风；`voice.start` 才准备 SpeechService，未安装语音能力不妨碍文字。

ProfileResources 唯一持有 Speech，Hub 只派生读取。语音中的 `text.submit` 取消旧 TTS，
`reset_input` 结清旧采集/STT/endpoint 与在途推理，清旧帧、废弃旧回调，再提交文字；
voice mode 和已有 worker/provider/detector 保持。普通 EOU 的 `finish()` 继续接受最终转写，
主动 abort 使用 `close()`。模型/许可静态检查只在激活或首次加载，不在逐帧推理中重复。

## Realtime 消息

| 方向 | 消息 | 约束 |
| --- | --- | --- |
| Pet → Backend | `text.submit {command_id,text}` | 文字或语音模式均可提交；语音模式保留并先 reset 旧采集。 |
| Pet → Backend | `voice.start / voice.stop {command_id}` | 显式切换采集与语音能力。 |
| Backend → Pet | `command.ack {command_id,status,code?}` | 接受或拒绝；拒绝不生成伪用户历史。 |
| Backend → Pet | `user.message {message_id,text,source}` | 已确认接收的用户消息；STT partial 保持内部。 |
| Backend → Pet | `clause.offer {clause_id,text,mode}` | 仅 `say_to_user`；mode 为 text 或 voice。 |
| Backend → Pet | `clause.audio_end / clause.cancel` | 当前语音句的数据结束或取消。 |
| Backend → Pet | `playback.pause / playback.resume` | VAD 播放闸门；暂停不等于交付。 |
| Pet → Backend | `renderer.ack {clause_id,complete}` | 完成交付或中断；不能跨会话/跨句使用。 |
| Backend → Pet | `agent.state {state}` | idle / listening / thinking / speaking。 |
| Backend → Pet | `session.ready / session.error` | 会话就绪或脱敏错误 code。 |

文字 `message_id` 使用提交的 `command_id`；先发 `user.message`，再发 accepted ACK，使 Pet 能先持久化已接收输入。二进制消息只用于语音：输入为 16 kHz、20 ms、640-byte PCM16LE，输出为当前句的 24 kHz PCM16LE。JSON 与 PCM 必须保留同一 WebSocket 的处理顺序。

## 可见交付与 Context

~~~mermaid
sequenceDiagram
  participant B as Main Agent
  participant P as Pet
  participant A as Audio output
  participant E as Electron history
  B->>P: clause.offer(text, mode)
  alt voice
    B->>P: PCM + clause.audio_end
    P->>A: 输出当前句
    A-->>P: 已越过最后样本输出位置
  end
  P->>E: recordDelivery(stable id, content)
  E->>E: SQLite 提交并广播可见消息
  E-->>P: 持久化成功
  P->>B: renderer.ack(complete=true)
  B->>B: 提交 assistant Context
~~~

Text 无需模拟 PCM，ACK 可提交 DRAFT clause；voice 需 audio_end 与完整播放后才提交 PLAYING clause。中断句不持久化/展示；历史写失败不得 ACK。重连、替换和取消必须使旧交付异步回调失效。

Electron 的 `conversation-history.sqlite3` 只包含已接收用户消息与已交付助手句。稳定 ID 去重、Profile 隔离和游标分页属于桌面投影，不回灌 Backend Context。

## Memory 与退出

~~~mermaid
flowchart LR
  INPUT["确认输入 / 已交付助手句"] --> CTX["Context conversation buffer"]
  CTX --> TIMER["空闲计时"]
  INPUT -->|新活动重置| TIMER
  TIMER -->|边界封存| PENDING["durable pending boundary"]
  PENDING --> WORKER["Memory 独立提炼 worker"]
  WORKER -->|成功| STORE["Memory SQLite / Qdrant"]
  STORE --> CLEAN["只清理已封存边界"]
  WORKER -->|失败 / 退出| RETRY["保留同 source_id 供重试"]
~~~

新输入可以重置未封存 timer，但不取消已封存 worker。提炼成功（包括零条记忆）后才清理相应 Context；失败或取消保留可重试边界。内部推理、Observation、Task 事实和未确认 partial 不作为用户可见对话归档。

退出按 realtime admission → 活动 Profile Runtime/Speech/Memory/存储 → TaskManager/Pi 顺序执行。Task 的持久状态与重启恢复归 TaskManager，不因桌面会话断开自动重跑。实际设备/Provider 验证情况见 [集成记录](frontend-integration-work.md)。
