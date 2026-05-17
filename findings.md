# 调研发现与决策

## 用户需求
- Supermemory 是闭源项目，AuraBot 不再接入 Supermemory。
- 记忆系统方向改为：节点存储信息，边存储信息之间的关联。
- 需要学习市面上的图结构开源记忆管理项目。
- 需要把现有关于 Supermemory 接入 AuraBot 的文档改掉。
- 需要写一份 HTML，阐述目前的记忆系统和改进方向。

## 外部调研发现
| 项目 | 可借鉴点 | 对 AuraBot 的启发 |
| --- | --- | --- |
| Graphiti / Zep OSS core | temporal context graph：实体、关系/事实、episode provenance，事实有有效期和被替代时间；Graphiti 是开源引擎，Zep 是托管平台。 | AuraBot 应把 `memory_edges` 从“联想边”升级为可带时间有效期、来源 episode、置信度和失效状态的事实边。 |
| Cognee | `remember / recall / forget / improve` 四类操作；支持 session memory 快速缓存并后台同步到永久知识图谱；定位为 memory control plane。 | AuraBot 可把写入流程拆成“即时会话记忆”和“后台图谱固化”，避免每轮聊天都做重型图谱整理。 |
| Mem0 | 通用 agent memory layer，支持 add/search；开源实现强调 hybrid search、filters、embedding 和多运行时集成；论文提到 graph memory 对多跳/时序问题有收益。 | AuraBot 后续可以引入 embedding/BM25/entity boosting，但应让它服务 seed node 选择，而不是替代结构化节点和边。 |
| Letta / MemGPT | core memory + archival memory 的分层模型，agent 通过工具自编辑记忆。 | AuraBot 可保留“短期上下文 / 长期节点图 / 摘要层”的分层，但不必让聊天模型直接任意改库，应继续通过受控 schema 和服务层写入。 |

## AuraBot 当前记忆系统发现
- 数据层已有 `memory_nodes`、`memory_edges`、`memory_summaries` 和 `event_logs`。
- 节点类型为 `trait / preference / event / follow_up`，状态为 `active / stale / completed / archived`。
- 边类型为 `relates_to / caused_by / scheduled_for / preference_for / opposes`，当前字段包含 source、target、relationType、weight、createdAt。
- 写入路径通过 `MemoryWriteQueue` 串行化，先由 merge planner 判断更新旧节点还是创建新节点，再由 relation planner 规划边。
- 召回路径先生成 recall plan，再用本地 scorer 选 seed nodes，之后通过 `GraphTraversal` 在边上做 bounded traversal。
- 当前已经有 Memory Debug trace：query、recall planner 输出、seed nodes、expanded nodes、returned nodes 和 traversed edges。
- 当前缺口：没有 episode/source provenance 表，没有边级 temporal validity，没有实体层，没有向量/BM25 索引，没有跨设备同步，没有系统化遗忘/压缩策略。

## 文档决策
| 决策 | 理由 |
| --- | --- |
| 将 `docs/supermemory-integration-guide.md` 改名并迁移为 `docs/memory/open-source-graph-memory-roadmap.md` | 文件名和目录都不再误导团队继续沿 Supermemory 集成方向推进。 |
| 保留并更新 `docs/memory/aurabot-memory-structure.md` | 这是当前系统现状说明，应补上“开源图记忆借鉴后的路线”。 |
| 新增 `docs/memory/memory-system-and-roadmap.html` | 满足用户要求的 HTML 汇报页，可直接浏览。 |

## 参考来源
- Graphiti GitHub README: https://github.com/getzep/graphiti
- Zep paper: https://arxiv.org/abs/2501.13956
- Cognee GitHub README: https://github.com/topoteretes/cognee
- Mem0 GitHub README: https://github.com/mem0ai/mem0
- Mem0 OSS migration / graph memory docs: https://docs.mem0.ai/migration/oss-v2-to-v3
- Letta docs: https://docs.letta.com/
