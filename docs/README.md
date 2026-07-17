# AuraBot 文档

本目录只保留当前开发和维护所需的资料。历史实施记录、视觉预览和已被替代的 v1 方案不再作为项目文档维护。

## 当前文档

| 文档 | 用途 |
| --- | --- |
| [产品需求](PRD-v2.md) | 产品定位、用户体验和非目标。 |
| [运行时模块](modules-v2.md) | Python 后端主运行时与 Electron 前端的目标边界。 |
| [后端迁移路线](backend-runtime-migration.md) | 从当前 TypeScript MVP 迁移到目标运行时的分阶段顺序。 |
| [运行时契约](backend-runtime-contract.md) | 配置、HTTP、WebSocket、数据所有权与错误格式的 v1 契约。 |
| [配置与后端生命周期](configuration-and-backend-lifecycle.md) | Python 解释器、配置、启动、退出、健康检查和失败恢复。 |
| [Main Agent v0 实现规格](main-agent-v0-implementation.md) | 第一个后端主消息与记忆闭环的实现边界和测试矩阵。 |
| [运行时数据所有权](runtime-data-ownership.md) | 迁移期间每类数据的唯一写入 owner、切换与回退规则。 |
| [当前状态](aurabot-development-status.md) | 当前基线、已落地能力、风险和下一阶段入口。 |
| [主 Agent](modules/main-agent.md) | 主对话 Agent 的职责、状态机和最小实现边界。 |
| [任务 Agent](modules/task-agent.md) | runtime task agent、状态、事件和安全边界。 |
| [TTS](modules/tts.md) | 语音合成选型和 dots.tts 接入策略。 |
| [Windows 适配](windows-platform-adaptation.md) | Windows 打包和运行时适配范围。 |

## 文档规则

1. README 只描述用户可运行的事实和最短启动路径。
2. PRD 描述产品目标；模块文档描述目标边界；迁移路线描述当前到目标的实施顺序。
3. 当前状态只记录可复核的事实、已验证命令和明确的下一步，不保存逐日开发日志。
4. 新实现先更新对应契约或模块文档，再更新当前状态；不要重新引入独立的历史状态板。
