# Tool SQLite 表格存储内容

> 对于 SQLite 的建表脚本必须是幂等的。
>
> 状态：这是 Python Tool / ToolGroup 的候选持久化模型，不是当前任务运行时的
> 已上线 schema。任务、artifact 和 Main Agent 的数据所有权以
> `docs/backend-runtime-contract.md` 为准；在接入真实数据库迁移前不得将本文档
> 当作运行时事实。


## 系统边界


AuraBot 整个项目只启动一个内部 MCP Server。所有 Tool 都由这个 MCP Server
实现并通过 MCP `tools/list` 暴露，Python Agent 通过同一个 MCP session 调用
`tools/call`。

因此 SQLite 不存储 MCP Server：

- MCP Server 的启动方式、transport 和 endpoint 属于应用代码或 `config.yaml`
- MCP Server 进程、连接和 session 只存在于运行时内存
- Tool 背后的 Python、Bash、HTTPS 等实现属于内部 MCP Server，不进入 Agent 数据库
- SQLite 只保存 Tool 定义缓存、用户配置和 ToolGroup 关系

如果未来允许用户添加多个 MCP Server，再新增 `mcp_server` 表，并给 `tool` 增加
`server_id` 外键。当前单 Server 约束下提前建立一张永远只有一行的 Server 表没有意义。


## Tool 元信息


- INTEGER/主键/自增：Tool ID
- TEXT/唯一/非空：MCP Server 返回的 Tool 名称
- TEXT：MCP Server 返回的可选展示标题
- TEXT：MCP Server 返回的 Tool 描述
- TEXT/JSON/非空：MCP Tool 输入 JSON Schema
- TEXT/JSON：MCP Tool 输出 JSON Schema
- TEXT/JSON/非空：MCP Tool annotations
- BOOL/非空：用户是否启用该 Tool
- BOOL/非空：最近一次发现时该 Tool 是否仍然可用
- TEXT：Tool 定义的内容哈希，用于检测 schema 变化
- TEXT：最近一次通过 `tools/list` 发现该 Tool 的时间

约束：

- 项目只有一个 MCP Tool 命名空间，因此 Tool 名称全局唯一
- Runtime Tool 通过 MCP 返回的 `name` 定位
- `title`、`description`、schema、annotations 和 `schema_hash` 是 MCP Server 数据的缓存
- `enabled` 是 AuraBot 自有配置，刷新 Tool 定义时不得覆盖
- Tool 暂时未被发现时设置 `is_available = 0`，不立即删除记录和分组关系
- Agent 只能调用同时满足 `enabled = 1` 和 `is_available = 1` 的 Tool


## ToolGroup 元信息


- INTEGER/主键/自增：ToolGroup ID
- TEXT/唯一/非空：ToolGroup 名称
- TEXT：描述信息
- BOOL/非空：ToolGroup 是否为项目原生配置

ToolGroup 是 AuraBot 对 Tool 的逻辑分组和任务授权单元，不代表 MCP Server。

一个 Tool 可以属于多个 ToolGroup。例如文件读取工具既可以属于 `filesystem`，也可以
属于只读任务使用的 `workspace-readonly`。因此使用 `tool_group_member` 多对多关联表，
而不在 `tool` 中直接保存 `group_id`。


## Tool 发现与刷新规则


内部 MCP Server 启动后，Agent 通过同一个 MCP session 调用 `tools/list`，并在一个
SQLite 事务内完成刷新：

1. 将所有已缓存 Tool 的 `is_available` 暂时设置为 `0`。
2. 使用 Tool `name` 插入或更新本次返回的 Tool。
3. 更新 MCP 管理的标题、描述、schema、annotations、哈希和 `last_seen_at`。
4. 将本次返回 Tool 的 `is_available` 设置为 `1`。
5. 保留用户管理的 `enabled` 和 `tool_group_member` 关系。

如果 `tools/list` 调用失败，本次刷新必须整体回滚，不能把所有 Tool 错误地标记为不可用。

Task Agent 只能调用同时满足以下条件的 Tool：

- Tool 的 `enabled = 1`
- Tool 的 `is_available = 1`
- Tool 属于当前 Task 获得授权的 ToolGroup


## 建表脚本

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    title TEXT,
    description TEXT,
    input_schema_json TEXT NOT NULL
        CHECK (
            json_valid(input_schema_json)
            AND json_type(input_schema_json) = 'object'
        ),
    output_schema_json TEXT
        CHECK (
            output_schema_json IS NULL
            OR (
                json_valid(output_schema_json)
                AND json_type(output_schema_json) = 'object'
            )
        ),
    annotations_json TEXT NOT NULL DEFAULT '{}'
        CHECK (
            json_valid(annotations_json)
            AND json_type(annotations_json) = 'object'
        ),
    enabled INTEGER NOT NULL DEFAULT 1
        CHECK (enabled IN (0, 1)),
    is_available INTEGER NOT NULL DEFAULT 1
        CHECK (is_available IN (0, 1)),
    schema_hash TEXT,
    last_seen_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tool_group (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    is_builtin INTEGER NOT NULL DEFAULT 0
        CHECK (is_builtin IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tool_group_member (
    group_id INTEGER NOT NULL,
    tool_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (group_id, tool_id),

    FOREIGN KEY (group_id)
        REFERENCES tool_group(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    FOREIGN KEY (tool_id)
        REFERENCES tool(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tool_group_member_tool_id
ON tool_group_member(tool_id);
```


## 不在这些表中存储的内容


- 内部 MCP Server 的 command、transport、endpoint 和启动参数
- MCP Tool 的 Python、Bash、HTTP 或其他具体实现
- API Key、Token、密码等 secret 明文
- MCP Server 子进程对象、MCP session 或网络连接对象
- Tool Call 的运行中状态和完整 stdout/stderr
- Task Agent 获得的临时 ToolGroup 授权

Tool Call 状态、结果、错误和产物应由 Task/Event/Artifact 所属模块记录，避免 Tool
注册表成为另一套任务运行时事实来源。
