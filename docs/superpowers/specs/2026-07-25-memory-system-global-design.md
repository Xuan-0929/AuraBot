# AuraBot 记忆系统全局设计

## 1. 目标

AuraBot 采用自研、本地优先的混合记忆系统。系统必须符合
`docs/PRD-v2.md` 和 `docs/modules-v2.md` 的以下约束：

- 记忆模块运行在 Python 后端主进程内，不是独立服务。
- 只有 chat-agent 可以直接管理短期记忆和读写长期记忆。
- 短期记忆同时进行内存级和磁盘级持久化。
- 上下文长度或记忆断点数量达到限制时，最早的完整断点间片段交给
  chat-agent 总结。
- 记忆模块不能绕过 chat-agent 自行判断并写入长期记忆。
- 长期记忆支持快速查询、选择性遗忘、冲突处理和来源追踪。
- 记忆权重不因内容变旧或变新而自动衰减。

存储固定为本地 SQLite 和 Qdrant：

- SQLite 是短期状态、长期事实、关系、审计和任务状态的真相源。
- Qdrant 是可重建的活跃长期记忆语义索引。
- embedding、抽取和重排模型通过 provider 接口注入。

## 2. 当前基线

仓库已经具备以下能力：

- `MemoryService` 提供 `remember / recall / forget / improve / due`。
- SQLite 保存 episode、memory item、entity、relation、audit 和 FTS 索引。
- Qdrant 本地模式保存长期记忆向量，并按 profile 和状态过滤。
- extractor、consolidator 和 retriever 已按职责拆分。
- 混合召回包含 vector、FTS、entity、relation、rerank 和 context packing。
- `ShortTermMemoryBuffer` 支持消息、话题断点、阈值切段和 JSON 持久化。
- `api.py` 暴露调试和测试用 HTTP 路由。
- `invode.py` 保留现有导入兼容。

当前主要缺口：

- 短期片段被切出后立即从 JSON 缓冲删除，没有可靠待处理状态。
- chat-agent 总结或长期写入失败时，片段可能丢失。
- 缺少领取、确认、释放、租约恢复和重试上限。
- 长期写入 SQLite 成功但 Qdrant 失败时，缺少显式索引修复队列。
- 生产模型 provider 和配置尚未形成完整闭环。
- recall 评估集和时间查询覆盖仍需扩充。
- Python chat-agent 尚未接入 memory 主链路。

## 3. 所有权和公开边界

生产业务调用固定为：

```text
connector / guard / scheduler / task-manager
                    |
                    v
                chat-agent
                    |
          +---------+----------+
          |                    |
          v                    v
ShortTermMemoryBuffer     MemoryService
```

约束如下：

- connector、guard-agent、scheduler 和 task-agent 不直接调用 `MemoryService`。
- guard-agent 只产生话题切换信息，由 chat-agent 写入短期记忆断点。
- task-agent 的结果先回到 chat-agent，再由 chat-agent 决定是否记忆。
- memory 不主动生成用户回复，也不直接推送 UI。
- `/memory/*` HTTP 路由保留为开发、测试和诊断入口。
- 生产前端和其他 Agent 不通过 HTTP 路由绕过 chat-agent。
- Python 内部调用以协议和 service 对象为主，不依赖 FastAPI。

## 4. 全局模块结构

目标目录按领域逐步演进：

```text
backend/memory/
  __init__.py
  invode.py
  api.py
  service.py
  types.py
  validators.py

  short_term/
    buffer.py
    models.py
    policy.py

  ingestion/
    extractor.py
    schemas.py
    providers.py

  retrieval/
    query.py
    seeds.py
    fusion.py
    relations.py
    ranking.py
    rerank.py
    context.py
    trace.py

  consolidation/
    consolidator.py
    strategies.py

  maintenance/
    index_repair.py
    scheduler.py
    diagnostics.py

  providers/
    embedding.py
    extraction.py
    rerank.py

  stores/
    sqlite.py
    short_term_sqlite.py
    qdrant.py
```

迁移采用小步方式。第一阶段只把当前 `short_term.py` 迁为兼容的
`short_term` 包，并新增独立短期 SQLite 仓储；不会同时移动所有现有模块。

### 4.1 API 和 façade

- `service.py` 是长期记忆业务入口。
- `short_term.buffer` 是主对话上下文和待总结片段入口。
- `api.py` 只把内部接口适配为调试 HTTP。
- `invode.py` 只重导出兼容符号，不实现业务逻辑。

### 4.2 Short-term

负责：

- 追加主对话消息。
- 记录由 chat-agent 提交的话题断点。
- 根据字符或 token 预算、断点数量判断是否需要切段。
- 原子生成最早完整断点间片段。
- 保存待总结片段的领取、确认、释放和失败状态。
- 在进程重启后恢复活动缓冲和未完成片段。

不负责：

- 识别话题是否切换。
- 调用总结模型。
- 判断片段是否值得进入长期记忆。
- 直接调用 `MemoryService.remember()`。

### 4.3 Ingestion

负责：

- 先写入来源 episode。
- 从 chat-agent 提交的内容或总结中抽取候选记忆。
- 校验 memory、entity 和 relation 数据。
- 写入 SQLite 长期记忆。
- 生成 Qdrant 索引数据。

每条派生记忆必须保留 episode provenance。短期片段生成的长期记忆还应在
episode metadata 中保存 `segment_id` 和断点信息。

### 4.4 Retrieval

召回流程固定为：

```text
query analysis
  -> Qdrant vector seeds
  -> SQLite FTS seeds
  -> entity seeds
  -> candidate fusion
  -> relation expansion
  -> SQLite hydration and filters
  -> rerank
  -> context budget packing
  -> trace
```

所有路径必须包含 `profile_id` 隔离。默认只返回 `active` 且在有效时间范围内的
长期记忆。Qdrant 得分不是最终唯一排序依据。

### 4.5 Consolidation

采用 ADD-first：

- 默认新增记忆，不静默覆盖旧记忆。
- 内容重复时保留来源并合并状态。
- 明确冲突时，新事实为 `active`，旧事实为 `superseded` 或 `stale`。
- 用户主动遗忘时进入 `archived`。
- consolidation 不按记忆年龄调整权重。

### 4.6 Maintenance

负责：

- Qdrant 索引修复队列。
- 后台 improve。
- due 事件查询。
- 数据健康检查、审计和 debug snapshot。
- failed 短期片段的显式重新入队。

maintenance 只维护数据，不直接提醒用户。到期事件仍交给 scheduler 和
chat-agent。

## 5. 存储设计

### 5.1 长期 SQLite

继续使用：

- `memory_episodes`
- `memory_items`
- `memory_entities`
- `memory_relations`
- `memory_audit_logs`
- `memory_items_fts`

后续增加：

- `memory_index_jobs`

`memory_index_jobs` 记录 Qdrant upsert/delete 的待执行、失败和完成状态，使
SQLite 写入不会因为向量索引暂时失败而丢失。

### 5.2 短期 SQLite

短期仓储单独实现于 `stores/short_term_sqlite.py`，使用同一个本地 SQLite
数据库文件或由工厂注入的连接配置，但不把 SQL 逻辑塞进 buffer。

表结构：

```text
short_term_messages
  id, profile_id, source_type, sequence,
  role, content, source_id, created_at

short_term_breakpoints
  id, profile_id, source_type, message_sequence,
  topic, reason, created_at

short_term_segments
  id, profile_id, source_type, breakpoint_id,
  state, payload_json, attempt_count, last_error,
  lease_owner, lease_expires_at,
  created_at, updated_at, completed_at
```

`payload_json` 是切段时生成的不可变消息快照。片段一旦创建，其内容不再随活动
缓冲变化。

### 5.3 Qdrant

collection 保持 `aurabot_memory_items`。

Qdrant 只保存 `active` memory item，payload 至少包含：

- `memory_id`
- `episode_id`
- `profile_id`
- `memory_type`
- `state`
- `entity_ids`
- `valid_from`
- `valid_to`
- `importance`
- `confidence`
- `source_type`

Qdrant 数据可以完全由 SQLite 重建，不作为事实来源。

## 6. 短期片段可靠交付

### 6.1 触发规则

满足任一条件时尝试切段：

- 当前短期上下文达到配置的 token 或字符预算。
- 当前未消费断点数量达到配置上限。

只有存在完整断点时才能切段。没有断点时即使上下文超限，也不能把当前话题从
中间截断；chat-agent 可以通过策略请求建立安全断点。

### 6.2 状态机

```text
pending
  -> processing
  -> completed

processing
  -> pending   release 或租约过期
  -> failed    达到重试上限

failed
  -> pending   显式 requeue
```

规则：

- 切段和从活动消息/断点中移除必须在同一 SQLite 事务完成。
- `claim_next_summary()` 原子领取 profile 下最早的 `pending` 片段。
- claim 写入 owner 和 lease 过期时间。
- `acknowledge_summary()` 幂等地完成片段，并可记录生成的 episode/memory ID。
- `release_summary()` 记录错误并增加尝试次数。
- 过期 lease 在下次 claim 或启动恢复时回到 `pending`。
- 超过配置重试次数后进入 `failed`，避免无限循环。

### 6.3 Chat-agent 合约

memory 向 chat-agent 返回结构化 `SummaryRequest`：

```text
segment_id
profile_id
source_type
topic
reason
messages
content
message_count
attempt_count
lease_expires_at
```

chat-agent 必须选择以下一种结果：

- 总结并调用 `MemoryService.remember()`，成功后 acknowledge。
- 判断不值得写长期记忆，带原因 acknowledge。
- 总结或写入失败，release 以便重试。

## 7. 长期记忆状态和一致性

memory item 状态：

```text
active
  -> superseded
  -> stale
  -> archived
```

SQLite 事务先提交长期事实和索引任务，再异步或同步尝试更新 Qdrant：

- Qdrant 成功：索引任务完成。
- Qdrant 失败：保留 SQLite 事实，索引任务等待修复。
- recall 发现索引候选在 SQLite 中已失效：过滤该候选并登记删除任务。
- forget 以 SQLite 状态为准，Qdrant 删除可重试。

对同一个 `segment_id` 的长期写入必须支持幂等检查，避免 chat-agent 在确认响应
丢失后重复生成同一批长期记忆。

## 8. 错误处理和可观测性

- SQLite 操作使用明确事务，禁止部分状态提交。
- 非法状态转换抛出领域错误，并写审计日志。
- extractor 非法输出不创建 memory item，但保留 episode 和错误记录。
- Qdrant 错误不伪装成长期事实写入失败。
- summary claim、ack、release、lease recovery 写入审计。
- debug snapshot 显示活动短期缓冲、pending/failed 片段、索引任务和最近 recall
  trace。
- 日志和 debug 输出不得跨 profile 暴露内容。

## 9. 测试策略

### 9.1 单元测试

- 断点和上下文阈值。
- 只切最早完整片段。
- pending/processing/completed/failed 状态转换。
- acknowledge 幂等。
- release 重试和重试上限。
- lease 过期恢复。
- profile 隔离。
- extractor schema 校验。
- consolidation 重复和冲突处理。
- retrieval fusion、rerank 和 trace。

### 9.2 存储测试

- SQLite migration。
- 切段事务回滚。
- 进程重启恢复。
- 同一片段只生成一次。
- Qdrant payload filter。
- 索引失败后修复。

### 9.3 集成测试

- 消息 -> 断点 -> pending -> claim -> remember -> acknowledge。
- 主 Agent 判断无需长期保存后 acknowledge。
- 总结失败 -> release -> retry。
- claim 后进程退出 -> lease recovery。
- remember -> recall -> forget -> recall empty。
- 两个 profile 相似内容不串库。
- 冲突偏好更新旧记忆状态。
- due 事件完成后不再作为 due 返回。

### 9.4 评估集

继续维护 `backend/tests/fixtures/memory/recall_cases.json`，覆盖偏好、别名实体、
跨轮事件、多跳关系、时间推理、冲突事实和遗忘。修改 retriever 或
consolidator 时必须运行评估。

## 10. 实施阶段

### 阶段 1：可靠短期片段

- 将 `short_term.py` 迁为兼容 package。
- 新建短期 models、policy 和 SQLite store。
- 实现 append、breakpoint、切段、claim、ack、release、requeue 和 lease recovery。
- 保持现有 `ShortTermMemoryBuffer` 导入路径。
- 增加短期状态机和重启恢复测试。

### 阶段 2：长期写入一致性

- 增加 `memory_index_jobs`。
- 让 remember/forget 以 SQLite 事务和索引任务为边界。
- 增加 `segment_id` 幂等 provenance。
- 实现 Qdrant rebuild/repair。

### 阶段 3：生产 provider

- 从统一后端配置构建 embedding、extractor 和 reranker。
- Hash 和规则 provider 仅作为测试或显式离线降级。
- 增加模型超时、非法输出和降级测试。

### 阶段 4：召回评估

- 扩展评估集。
- 校准 vector、FTS、entity 和 relation 融合。
- 完善时间过滤和当前/未来事件排序。
- 固定 context budget 行为。

### 阶段 5：维护闭环

- 完善 duplicate/conflict consolidation。
- 完善 due 完成状态。
- 扩展 debug、audit 和健康检查。
- 增加后台 improve 和索引修复调度入口。

### 阶段 6：接入 Python chat-agent

- chat-agent 串行管理短期记忆。
- guard-agent 通过事件提交断点。
- chat-agent 领取、总结并确认片段。
- recall 结果进入主模型上下文。
- task-agent 结果只经 chat-agent 决定是否记忆。
- 生产前端停止直接写长期记忆。

## 11. 当前实施范围

当前立即实施阶段 1。

阶段 1 不包含：

- chat-agent 总结模型实现。
- 自动写长期记忆。
- 真实 embedding/extractor 接入。
- Qdrant 索引修复队列。
- 前端调用迁移。

阶段 1 的完成标准：

- 现有 memory 测试继续通过。
- 新增可靠片段状态机测试通过。
- 片段在失败和进程重启后不会丢失。
- profile 数据严格隔离。
- `ShortTermMemoryBuffer` 不直接依赖 `MemoryService`。
- 模块职责与本设计一致。
