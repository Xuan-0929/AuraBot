# TTS 模块

## 决策

AuraBot 的 TTS 是可选能力，不是桌面端默认安装依赖。主对话 Agent 先完成文字
分句和事件输出；语音引擎只消费已完成的句子，不参与主对话、记忆或任务决策。

## dots.tts 评估

`dots.tts` 提供 48 kHz 输出、参考音频声音克隆和 `mf` / `soar` 两种面向运行的
模型：

| 模型 | 定位 | AuraBot 策略 |
| --- | --- | --- |
| `dots.tts-mf` | 较低延迟 | 仅作为具备合适加速硬件时的可选引擎。 |
| `dots.tts-soar` | 较高质量与音色稳定性 | 仅作为高性能设备上的可选引擎。 |
| `dots.tts-base` | 微调基础模型 | 不进入桌面端 MVP。 |

本地 macOS CPU 试验中，`dots.tts-mf` 以 `float32` 成功生成 5.12 秒、48 kHz
单声道 WAV，但生成耗时约 32.4 秒，RTF 约 6.34；默认 `bfloat16` 在 CPU 路径会
出现 dtype 不匹配。因此它不满足默认实时桌宠交互的要求。

## 运行时边界

1. 不把模型权重、Python 环境或生成的音频纳入应用安装包或 Git 仓库。
2. 本地 TTS 以独立 worker 运行，通过受控的本地接口接收短句并返回音频文件或流。
3. worker 由用户显式安装、启用和选择模型；启动前检测硬件、模型路径、磁盘空间和可用精度。
4. 无可用 TTS worker 时，聊天和任务仍正常工作，只禁用语音播放并给出简短状态提示。
5. 参考音频和克隆音色属于敏感用户资产；必须本地保存、显式授权，且提供删除入口。

## 接入顺序

1. Main Agent 输出句级 `chat.segment.completed` 事件。
2. 定义 `SpeechSynthesizer` 接口和音频产物生命周期。
3. 接入一个稳定的短句 TTS fallback，再把 dots.tts 作为硬件能力满足时的 provider。
4. 最后实现播放队列、打断、缓存、音量和设置页。

在主消息 WebSocket 和句级事件稳定前，不接入 dots.tts worker。
