# 调研发现与决策

## 用户需求
- 为目前 AuraBot 的记忆系统写一份详细、可读性强的流水线分析。
- 文档需要加入示例。
- 产物需要是 HTML，放在 `docs/memory/` 文件夹里。

## 当前记忆系统流水线发现
- 入口在 `ChatOrchestrator.sendMessage()`：先保存用户消息，再让 `gateModel` 判断是否回复、是否写记忆、输入类型是否为聊天/任务/任务控制。
- 只有普通聊天路径会进入记忆写入和召回；任务和任务控制路径会跳过长期记忆写入。
- 写入触发条件是 `gateDecision.memoryDecision === "write_now"`。写入文本使用 `semanticSegment`，即 gate 对聊天上下文合并后的语义段。
- 抽取器是 `MemoryExtractor`。生产配置里应由 `memoryModel` 做结构化抽取；测试或显式 fallback 可用 `DeterministicMemoryExtractor`，规则能识别喜欢/讨厌偏好、带时间词和事件词的 event、以及提醒类 follow_up。
- `MemoryWriteQueue` 用 `p-queue` 串行化写入，避免同一时间多个记忆写入同时读旧上下文、改节点和补边。
- `MemoryService.enqueueWrite()` 先读取完整 profile 记忆上下文，再由 `MemoryMergePlanner` 判断创建新节点或更新旧节点。
- 创建新节点时，`MemoryRelationPlanner` 会基于新节点和已有节点规划边；确定性规则通过共享概念连边，event/follow_up 与 preference 之间优先生成 `preference_for`。
- `ChatOrchestrator.writeMemoriesFromMessage()` 会把同一轮中 follow_up 的 `relationToPrevious` 指向前一个 event，形成 `scheduled_for` 边。
- `MemoryRepository` 把节点写入 `memory_nodes`，边写入 `memory_edges`，摘要写入或追加到 `memory_summaries`；重复同 type + content 且非 archived 的节点只提升 importance 和 updated_at。
- 召回入口是 `MemoryService.retrieveRelevant(profileId, query)`，先由 `MemoryRecallPlanner` 给出 queryTerms、targetTypes、rankedNodeIds，再由本地 scorer 选择最多 4 个 seed nodes。
- scorer 依据直接文本命中、概念命中、类型偏权、模型 targetTypes 和 importance 打分。
- 选出 seed 后，`retrieveMemoryContext()` 使用 `GraphTraversal` 沿边双向、有界扩散，默认最大深度 2，最多扩展 8 个节点。
- 召回结果包含 summaries、nodes 和 trace；`MemoryService` 保存最近一次 trace，Memory Debug 面板展示 query、queryTerms、targetTypes、seed/expanded/returned 数量和 traversed edges。
- 到期 follow-up 有两条触达路径：主聊天回复时插入 `dueFollowUps`，以及 `MemoryFollowUpScheduler` 后台轮询并推送桌宠气泡；触发后调用 `completeFollowUps()` 标记为 completed。
- 固定评估入口 `corepack pnpm eval:memory` 覆盖直接偏好召回、event 到 preference 的图跳转、due follow-up 过滤。

## 示例设计
- 示例使用三轮对话：
  1. “我喜欢吃火锅。”写入 preference。
  2. “我明天要和阿杰去吃火锅，提醒我别忘了。”写入 event 和 follow_up，并连到 preference。
  3. “阿杰那件事我该顺便注意什么？”召回 event seed，并沿 `preference_for` 扩散到 preference。
- HTML 应展示每阶段输入、处理、输出和可观察日志。
- HTML 应解释当前边界：没有 embedding/BM25、没有实体层、没有 episode provenance，边没有状态/有效期/置信度字段。

## 文档决策
| 决策 | 理由 |
| --- | --- |
| 新文件命名为 `docs/memory/memory-pipeline-analysis.html` | 与已有 roadmap 页区分，直接表达“流水线分析”。 |
| 页面采用静态 HTML + 内联 CSS + SVG/表格 | 可直接打开，不依赖构建，也适合团队成员阅读。 |
| 重点写“从一条消息进入系统到写入/召回/提醒/调试”的端到端路径 | 比单独罗列表结构更容易理解系统行为。 |
