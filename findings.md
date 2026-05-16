# 调研发现与决策

## 用户需求
- 用户要求写一份文档说明当前任务机制。
- 文档需要分析可改进点和优化策略。
- 文档必须基于当前代码实现，而不是泛泛讨论任务系统设计。
- 用户团队以中文协作为主，文档和过程记录需要中文化。

## 调研发现
- 初始文件列表显示，任务实现主要集中在 `src/core/tasks`，任务数据结构在 `src/shared/schemas/task.ts`，持久化在 `src/core/storage/repositories/taskRepository.ts`，聊天编排触点在 `src/core/orchestration`，渲染进程展示在 `src/renderer/src/features/tasks` 和桌宠任务便签组件。
- 任务状态包括 `queued`、`running`、`waiting`、`completed`、`failed`、`interrupted`。当前执行路径主要使用 `queued`、`running`、`completed`、`failed`、`interrupted`；`waiting` 被视为活跃状态，但核心执行器暂未主动产生。
- 任务类型包括 `result_output` 和 `time_reminder`。结果产出型任务可由命令行子执行器处理；时间提醒型任务保持排队状态，到期后由主动触发引擎完成。
- `TaskRepository` 保存精简任务行和追加式任务事件。事件摘要目前写在 `payload_json` 内，读取事件时再从该字段恢复。
- `TaskService` 负责从聊天创建任务、修改任务、运行任务、后台运行、打断、控制台取消/删除、完成到期提醒，以及启动时恢复失联任务。
- `MultiCliTaskRunner` 会按优先级选择第一个可用执行器，校验 Codex 工作目录是否为 Git 仓库，启动子进程，将 Codex 的 JSONL 进度写入任务事件，并把最终输出写入文件。
- `TaskControlRouter` 使用中文关键词正则和简单标题匹配推断查询、启动、打断、修改等控制意图。
- `TaskPresentation` 根据任务状态和事件生成聊天回复，并从事件中的文件改动或输出文件字段推断附件。
- `ChatOrchestrator` 在门控判断返回 `inputKind=task` 时创建任务；当 `taskKind=time_reminder` 且时间解析成功时创建提醒任务；当 `inputKind=task_control` 时调用任务控制方法。
- 自动执行不在 `ChatOrchestrator` 内触发，而是在 `registerIpc` 中，当聊天结果包含新建的结果产出型任务且任务执行器配置允许自动运行时，调用 `runTaskInBackground`。
- 后台执行会通过 `task:changed` 广播进度，完成或失败后创建回流聊天消息、发送桌宠活动，并把全局代理状态置为空闲。
- 渲染进程初始状态会加载所有任务，监听 `task:changed` 和 `task:deleted`，支持控制台取消/删除，并在桌宠和控制台展示任务便签。
- 主动提醒逻辑会轮询到期的 `time_reminder` 任务，发送桌宠/系统通知，将任务标记完成，并广播任务变化；该提醒不会主动写入聊天历史。
- 配置层暴露 Codex、Claude Code、Hermes 三类命令行执行器的启用状态、优先级和超时；内部配置还支持命令路径、工作目录、模型、安全沙盒、权限策略和附加参数。
- 现有测试覆盖任务生命周期、后台进度、失败、取消、失联恢复、命令行执行器选择和回退、Codex JSONL 进度、展示文案、任务控制匹配、到期提醒完成等关键路径。

## 技术决策
| 决策 | 理由 |
| --- | --- |
| 使用 `docs/task-mechanism-analysis.md` 作为交付路径 | 该目录是现有项目文档目录，命名也能直接说明内容。 |
| 按阶段组织优化建议 | 当前系统已经有可用闭环，阶段化建议能区分低风险收口和深层执行器/调度改造。 |

## 遇到的问题
| 问题 | 处理方式 |
| --- | --- |

## 参考资源
- `src/core/tasks/*`
- `src/shared/schemas/task.ts`
- `src/core/storage/repositories/taskRepository.ts`
- `src/core/orchestration/chatOrchestrator.ts`
- `src/main/registerIpc.ts`
- `src/renderer/src/features/tasks/*`
- `tests/integration/tasks/task-lifecycle.spec.ts`
- `src/core/proactive/proactiveTriggerEngine.ts`
- `src/core/bootstrap/loadConfig.ts`
- `tests/unit/core/tasks/cliTaskRunner.spec.ts`
- `tests/unit/core/tasks/taskPresentation.spec.ts`
- `tests/unit/core/proactive/proactiveTriggerEngine.spec.ts`

## 视觉或浏览器发现
- 本次任务没有使用视觉或浏览器信息。
