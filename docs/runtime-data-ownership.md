# 运行时数据所有权

## 目标

在 Python runtime 迁移期间，每类数据只能有一个写入 owner。读取副本、缓存和调试
快照可以存在，但不能形成第二条业务写链路。

## 所有权矩阵

| 数据 | 当前 owner | Phase 0-1 owner | 目标 owner | 迁移规则 |
| --- | --- | --- | --- | --- |
| 窗口位置、点击穿透、渲染设置 | Electron | Electron | Electron | 不迁移，除非跨渠道设置需要同步。 |
| Electron 模型与任务 CLI 配置 | Electron JSON | Electron JSON | 待 Phase 0 显式合并 | Python 不隐式读取。 |
| Python 网关配置 | `config.yaml` | `config.yaml` | 统一运行时配置 | 仅通过显式配置迁移工具合并。 |
| 现有聊天、任务与事件 SQLite | TypeScript core | TypeScript core | Python runtime | 在切换某条垂直链路前保持只读。 |
| Main Agent session、短期片段、pending 状态 | 无 | Python `MainAgentStateStore` | Python Main Agent | 新建独立存储，不回写 legacy 表。 |
| 长期记忆与向量索引 | Python `MemoryService` 试验存储 | Python `MemoryService` | Python Main Agent 经 MemoryService | 只有 Main Agent 可调用写入。 |
| task runtime 状态与 artifact | TypeScript core | TypeScript core | Python task-manager | 等 Phase 3 统一迁移。 |
| 模型密钥 | 本地配置 / 环境变量 | 各 runtime 显式配置 | 单一受控配置 | 不记录到数据库、日志或事件 payload。 |

## 迁移原则

1. 先迁移写入，再让 UI 改读新 owner；不做长期双写。
2. 每条迁移链路都要有明确的 `cutover` 版本和回退条件。
3. 迁移前 legacy 数据只能由新 runtime 只读导入；新 runtime 写入后，legacy runtime 不得修改该对象。
4. 任务、消息、记忆和配置的迁移互相独立，禁止用一次数据库“大迁移”绑定所有风险。
5. 所有用户可见对象使用稳定 UUID，不依赖 SQLite `rowid`、文件路径或运行时内存地址。

## Main Agent v0 数据边界

v0 新建的 `main_sessions`、`main_events`、`short_term_segments` 与 `pending_actions`
只服务 Python Main Agent，不与 Electron 的 `sessions`、`messages` 表混合写入。这样可以
验证后端队列、记忆与恢复行为，而不会损坏当前 MVP。

当 Phase 2 将一条桌面消息链路切到后端时：

1. Electron 生成 `event_id` 和 `client_message_id`，提交给后端。
2. Python Main Agent 写入自己的消息/事件记录并成为该消息的 owner。
3. Electron 只保留渲染投影，不把同一消息再写回 TypeScript core。
4. 读取历史时按 session 的 `owner` 选择一个来源；不在 UI 层按时间拼接两套数据库。

## 一致性与失败处理

| 情况 | 规则 |
| --- | --- |
| HTTP 重试 | 使用 `(profile_id, client_message_id)` 幂等；原请求结果可重复返回。 |
| 进程在写入中退出 | 事务未提交则不产生记录；已提交的 event 由重启恢复逻辑处理。 |
| 记忆写入失败 | 主消息和短期片段保留，创建 pending action；不回滚用户已看到的消息。 |
| 切换失败 | 关闭对应 cutover feature flag，恢复 legacy owner；不得复制或删除用户数据。 |
| 数据库版本不兼容 | 拒绝启动该写链路，展示 `SCHEMA_VERSION_MISMATCH`，只允许安全的只读诊断。 |

## 数据库迁移要求

- 每个 Python schema 变更必须有单调递增版本、幂等迁移和向前检查。
- 迁移在事务中执行，失败时回滚；大型数据复制拆成可恢复批次。
- 迁移日志只保存版本、时间和摘要，不能保存敏感内容。
- artifact 文件与数据库登记分两步处理：先写临时位置，再原子移动并登记；失败时清理临时文件。

## 验收标准

1. 对任一消息、记忆、任务或配置字段，都能从本表指出唯一写 owner。
2. Main Agent v0 不会修改 Electron legacy 业务表。
3. 幂等提交、重启恢复、迁移失败和 feature-flag 回退都有自动化测试。
4. 不存在通过 UI 拼接两个消息 owner 的历史视图。
