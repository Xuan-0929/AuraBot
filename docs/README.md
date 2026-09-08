# AuraBot 文档

当前 Profile/Live2D 契约以 2026-09-08 重构为准，其他业务继续沿用 2026-09-06 架构收敛。历史设计不得覆盖下列当前契约。

| 文档 | 信息所有权 |
| --- | --- |
| [Profile 与 Live2D](profile-live2d.md) | 独立目录、迁移、三层系统、资源配置、SAY 作用域和播放器时钟。 |
| [产品需求](PRD-v2.md) | 用户体验、范围、非目标与验收边界。 |
| [系统模块](modules-v2.md) | Electron、Pet、Backend 与存储的唯一 owner。 |
| [配置与生命周期](configuration-and-backend-lifecycle.md) | settings.json、保存/应用、开发启动、忙碌重启与数据布局。 |
| [后端契约](backend-runtime-contract.md) | text/voice realtime、交付回执、readiness 与重启协议。 |
| [Pi Task Runtime](modules/task-agent.md) | Task 状态机、Pi 控制面与执行面。 |
| [语音资产](main-agent-speech-assets.md) | 可选依赖、模型和许可证安装。 |
| [架构优化记录](architecture-optimization.md) | 本轮架构变化、工作量对照、检查与真实设备验收边界。 |
| [当前状态](aurabot-development-status.md) | 集成范围与尚未验证的边界。 |
| [本轮集成记录](frontend-integration-work.md) | 已批准要求、实际测试/桌面检查及剩余工作。 |
| [Windows 适配](windows-platform-adaptation.md) | 平台与生产打包限制。 |
| [Main Agent V1 历史设计](modules/main-agent.md) | 旧语音入口设计及 Runtime 内部设计参考；入口/配置契约已被当前文档取代。 |

开发启动从仓库 [README](../README.md) 开始。文档明确区分代码实现、自动测试和真实 Provider/设备验收，不把保留的旧设计描述为当前可用功能。
