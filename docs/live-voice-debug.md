# Live 语音 Debug

在「设置 → 桌宠状态」勾选「Live 语音 Debug 模式」即可立即打开独立窗口。该开关独立保存，不应用设置页其他修改，不重启后端，也不会自动开启麦克风。

默认窗口为 1120 × 760，受当前屏幕工作区限制。左侧事件列表占据主要阅读空间，按来源、阶段、级别及关键词筛选；点击事件在右侧查看完整详情。右侧有「当前状态」「对话文字」「事件详情」三个标签，字段和长文本使用整个侧栏滚动。宽度小于 900px 时通过「事件／检查面板」切换，两边保留各自的阅读位置。

「跟随最新」只在新事件到来时滚动。向上阅读、选择事件或翻到较早页会暂停跟随，后台继续采集；点击新事件提示或「最新」恢复。暂停时固定所读事件范围，避免新事件推动旧页漂移。较早记录移出内存会明确提示，已选事件的详情保留到重新选择或清除日志。

关闭窗口后继续采集和写日志；通过设置中的打开按钮重新查看。取消勾选停止采集，保留已保存日志。应用重启恢复开关，但麦克风仍须显式开启。

```mermaid
flowchart LR
  Pet[桌宠：采集与设备播放] <-->|已有语音 WebSocket| Backend[后端：识别、断句、模型、合成]
  Backend --> Buffer[有界内存诊断缓冲]
  Buffer -->|500ms 增量读取| Main[Electron Main]
  Pet -->|受校验 IPC| Main
  Main --> Window[独立 Debug 窗口]
  Main --> Logs[有界 JSONL 滚动日志]
```

## 日志和清除

日志位于当前应用数据根目录的 `logs/live-debug/`；点击「打开日志目录」即可访问，无需手动寻找不同系统的数据目录。

- 每个文件最多 5 MiB，保留 `live-debug.jsonl`、`.1`、`.2`，总共最多三个文件。
- 窗口显示本次应用运行最近的记录；较早运行的记录在滚动文件中查看。
- 前后端缓存各限制为 1,000 条、2 MiB，单个事件最多 32 KiB；截断与丢弃有标记。
- 「清空 Debug 日志」清除本功能的三个文件和内存历史，继续当前采集；不会删除聊天历史、其他日志或中断 live 对话。
- 文件写入失败时提示错误，继续提供有界内存查看。异常崩溃可能丢失最后尚未读取或落盘的一小批记录。

日志包含识别文字和助手回复。诊断仅收集白名单标量字段，不记录原始音频、密钥、设备 ID、完整模型上下文或通用工具参数与返回正文。

## 如何判断卡住的阶段

- 传输 `active` 仅代表会话建立；麦克风是否开启另看采集状态。
- RMS、噪声门限、VAD 概率和确认时长用于判断输入为什么未被认定为讲话。
- `partial` 与 `sentence_end` 是 STT 的中间／结束结果；EOU 另行决定是否结束话轮，fallback 也有事件记录。
- PREPARE、RESPOND、CONTINUE、准入原因、首片段和结算结果用于判断模型处理阶段。
- 后端的语音分句 offer／发送与前端 `devicePlayback.physical.started` 是不同事实。
- `devicePlayback.physical.completed`、`history.write.completed`、`renderer.ack.sent` 和后端 accepted 可用于定位交付问题。
- 记录仅从 debug 开启时开始；开启时补充当前快照，不补造过去的事件。缺失的观测不等于成功。

## 实现约束

### 首音延迟的细分事件

模型阶段新增以下事件，沿同一个 `inputId`、`invokeId`、`sayId` 关联：

```mermaid
flowchart LR
  A[provider.request_started] --> B[first_chunk]
  B --> C[first_tool_delta]
  C --> D[say.first_text]
  D --> E[say.clause_ready]
  E --> F[say.authorized]
  F --> G[tts queued / first_audio]
  G --> H[devicePlayback.physical.started]
```

`provider.request_started` 包含实际请求的 `modelName`；`first_chunk` 仍是已有的首个解析后 SSE 数据块。`say.first_text` 表示增量解析出首段非空 SAY 文本，尚不代表授权交付。`say.clause_ready` 表示该分句已满足工具身份与顺序要求、即将检查执行权限；`say.authorized` 位于权限检查之后、交付调用之前。多个事件可以来自同一个 SSE 数据块，不应据此推算服务端逐 token 时间。

分句事件记录 `charCount` 和 `flushReason`：`hard_punctuation`、`soft_boundary`、`length_limit`、`object_closed`。现有 24/80 字符阈值保持不变。SAY 事件的 `elapsedMs` 起点是本次 Harness 开始消费流；跨阶段比较优先使用同一调用内事件差值，不把首 SSE 到首分句的全部时间称作分句等待。

STT 正常收尾先停止接收新发送并等待在途发送，再等待最终回调；正常完成预算暂为 1 秒，取消清理仍为 250ms。`finish_ended.elapsedMs` 用于后续真机校准。收尾或提交期间新到的讲话会等待当前端点结算，再开始新 segment；现有麦克风队列保留最多 50 帧，复测时仍需检查 `droppedFrames`，尤其是极慢服务与连续插话场景。

STT 失败会使当前未确认输入失效，并通过现有错误通道提示重说；不拿 partial 执行操作。底层 SDK 阻塞线程不能被 Python 强制停止，超时后仍由唯一 stop 任务持有；迟到转写被隔离，不启动第二个并行 stop。

本轮模型提示词实验要求首句短、完整、带句末标点；耗时读取前在同一调用说明具体下一步，简单聊天优先利用现有上下文。它不保证模型必定遵循，也不代表已经测得首音改善。建议用相同模型配置复测问候、自我介绍、读取任务、拒绝恢复任务和连续插话，分别记录有效确认、实质答案及失败次数。TTS 预合成和模型配置调整留待新日志提供依据。

诊断是观测层，不修改 VAD/EOU 阈值、语音打断、TTS 背压或 ACK 次序。后端没有新增常驻采集任务，声学和音频计数最多每 500ms 汇总一次。Main 使用不重叠的 HTTP 请求，不占用语音 WebSocket 发送锁。关闭采集后不读取事件；若停用控制请求暂时失败，只重试停用同步。

Main 为合并时间线分配 `id`；事件保留来源进程、序号、采集批次、Profile、generation 和关联分句／调用身份。阶段耗时由同一进程的单调时钟计算，不跨进程相减。清除和会话替换会撤销旧身份，迟到数据不能恢复已清除历史。
