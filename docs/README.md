# AuraBot 文档

本目录只维护当前事实与目标设计。旧 Task Runtime 不另建 archive，Git 历史即档案。

| 文档 | 信息所有权 |
| --- | --- |
| [产品需求](PRD-v2.md) | 产品定位、目标能力和非目标。 |
| [系统模块](modules-v2.md) | 进程与模块边界。 |
| [Pi Task Runtime](modules/task-agent.md) | 已批准第二轮 Task 架构与实现状态的唯一事实源。 |
| [Main Agent](modules/main-agent.md) | Main Agent 如何调用 Control Plane。 |
| [后端契约](backend-runtime-contract.md) | 固定 health 与版本化 Task REST/SSE 边界。 |
| [后端迁移](backend-runtime-migration.md) | 从空窗期到后端垂直切片的实施阶段。 |
| [数据所有权](runtime-data-ownership.md) | 各类数据的唯一写入 owner。 |
| [配置与生命周期](configuration-and-backend-lifecycle.md) | Python 配置、启动、健康与退出。 |
| [当前状态](aurabot-development-status.md) | 可复核的实现事实和下一步。 |
| [Windows 适配](windows-platform-adaptation.md) | 平台与打包边界。 |
| [TTS](modules/tts.md) | TTS 选型与接入策略。 |

文档必须区分“已实现”“已删除”“已批准待实现”和“明确延期”。README 只描述用户
当前能运行的能力；Pi 细节不得复制到其他文档。
