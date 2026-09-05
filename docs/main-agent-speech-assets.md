# Main Agent 语音资产与可选依赖

Backend 的语音能力默认 `enabled: false`。默认启动不会导入 DashScope、ONNX Runtime、
NumPy 或 Transformers，不会下载模型，也不会发出付费 Provider 请求。通过设置页保存并应用语音配置后，仍须用户显式开启麦克风，`voice.start` 才准备语音资源。缺依赖、模型、许可证或密钥时拒绝该能力，文字会话与设置仍可用。

## 可选依赖

~~~bash
uv sync --project backend --extra speech
~~~

`speech` extra 安装 DashScope SDK、ONNX Runtime、NumPy 和用于 Smart Turn Whisper 特征的
Transformers。普通 Backend 与单元测试不需要这些包。

## 支持的资产契约

Electron 根据仓库位置生成以下资源的绝对路径，保存在 `settings.json` 的 `backend.main_agent.speech` 段；设置页可修改模型与许可证路径。无需创建 YAML 配置：

- `assets/models/silero-vad.onnx`：官方 16 kHz Silero VAD ONNX；每次推理 512 个新样本，
  加 64 个上下文样本，`state` 为 float32 `[2, 1, 128]`，许可证为 MIT。
- `assets/licenses/silero-vad.txt`：随采用版本保存的完整 Silero MIT license 文本。
- `assets/models/smart-turn-v3.2.onnx`：Smart Turn v3.2；最近 8 秒 16 kHz 单声道音频经
  Whisper feature extractor 转为 float32 `[1, 80, 800]`，许可证为 BSD-2-Clause。
- `assets/licenses/smart-turn-v3.2.txt`：随采用版本保存的完整 BSD 2-Clause license 文本。

仓库不携带大型 ONNX 文件。部署者应从上游发布获取模型，把许可证全文与模型一起保存，
并在部署清单记录上游 URL、release/tag、下载日期以及 SHA-256。下载后先验证清单中的哈希：

~~~bash
shasum -a 256 assets/models/silero-vad.onnx
shasum -a 256 assets/models/smart-turn-v3.2.onnx
~~~

只有模型文件存在、对应许可证文件存在且含预期许可证标记时，本地 detector 才报告
`ready_to_load`；ONNX 输入/输出不符合上面的固定契约时报告 `model_load_failed` 或
`inference_failed`，不会让 Backend 启动失败。哈希值必须来自所选上游 release，不能用
未注明版本的通用值替代。

真实 DashScope、麦克风和音频输出设备 smoke 是独立验收，可能产生 Provider 费用；普通测试使用注入 transport/fixture，不证明真实设备或凭据可用。实际验证结果见 [本轮集成记录](frontend-integration-work.md)。
