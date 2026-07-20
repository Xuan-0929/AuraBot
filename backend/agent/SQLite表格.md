# Agent SQLite 表格存储内容

> 对于SQLite的建表脚本必须是幂等的。
>
> 状态：这是 Python Skill / SkillGroup / MCP Server / ToolGroup / Tool 的候选持久化模型，不是当前任务运行时的
> 已上线 schema。任务、artifact 和 Main Agent 的数据所有权以
> `docs/backend-runtime-contract.md` 为准；在接入真实数据库迁移前不得将本文档
> 当作运行时事实。


## Skill元信息


- INTEGER/主键/自增：Skill ID
- INTEGER/外键/非空：属于的SkillGroup ID
- TEXT/非空：Skill名称
- TEXT/非空：Skill在本机的URL或者基于项目根目录的URL。
- BOOL/非空：Skill是项目原生的还是后续用户安装的
- BOOL/非空：Skill是基于全局URL的还是基于项目根目录的URL。

约束：

- 同一个SkillGroup内Skill名称唯一：`UNIQUE (group_id, name)`
- 不同SkillGroup之间允许Skill名称重复




## SkillGroup元信息


- INTEGER/主键/自增：SkillGroup ID
- TEXT/唯一/非空：SkillGroup名称
- TEXT：描述信息
- BOOL/非空：SkillGroup是项目原生的还是后续用户安装的


## 建表脚本

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS skill_group (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS skill (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
    is_global_url INTEGER NOT NULL DEFAULT 0 CHECK (is_global_url IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (group_id)
        REFERENCES skill_group(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    UNIQUE (group_id, name)
);

CREATE INDEX IF NOT EXISTS idx_skill_group_id
ON skill(group_id);

CREATE INDEX IF NOT EXISTS idx_skill_name
ON skill(name);
```


# MCP Server / ToolGroup / Tool


## 系统关系


Tool Server、ToolGroup 和 Tool 是三个不同层级：

```text
MCP Server
└── ToolGroup（一个 Server 可以包含多个 Group）
    └── Tool（一个 Group 可以包含多个 Tool）
```

AuraBot 自带一个原生 MCP Server。这个 Server 可以包含多个原生 ToolGroup，例如
`filesystem`、`shell` 和 `web`。以后用户注册的外部 MCP Server 也可以拥有一个或多个
非原生 ToolGroup。

三者的职责分别是：

- MCP Server 负责 Tool 的实现、连接和实际调用。
- ToolGroup 负责组织 Tool，并作为 Main Agent 分配给 Task Agent 的运行时授权单位。
- Tool 是最终暴露给 Agent 的可调用能力。

ToolGroup 不代表 MCP Server；`is_native` 也不表示 Server 类型。它只表示该
ToolGroup 是否为 AuraBot 随项目提供的原生工具组。

本设计规定一个 Tool 只属于一个 ToolGroup。一个 Tool 是否为项目原生 Tool，由它所属
ToolGroup 的 `is_native` 决定，因此不在 `tool` 表重复保存 `is_native`。

MCP Server 的身份和 URL 保存在独立的 `mcp_server` 表中。ToolGroup 通过
`server_id` 归属于一个 MCP Server，Tool 再通过 `server_id + group_id` 同时确定调用
来源和授权分组。


## MCP Server 元信息


- INTEGER/主键/自增：MCP Server ID
- TEXT/唯一/非空：MCP Server 名称
- TEXT/唯一/非空：MCP Server URL
- TEXT：MCP Server 功能说明

约束：

- AuraBot 自带的 MCP Server 和用户注册的外部 MCP Server 都保存在本表。
- 当前版本只登记可以通过 URL 连接的 MCP Server。
- `name` 是 AuraBot 管理和界面展示时使用的 Server 名称，不是 Tool 名称。
- `url` 是 MCP endpoint；同一个 URL 不能重复注册。
- URL 中不得包含 API Key、Token、用户名或密码。
- Server 是否为项目原生不在本表重复记录，由它所包含的 ToolGroup 的 `is_native`
  表达。
- 已经包含 ToolGroup 的 MCP Server 不能直接删除，必须先显式处理其 ToolGroup 和 Tool。


## ToolGroup 元信息


- INTEGER/主键/自增：ToolGroup ID
- INTEGER/外键/非空：所属 MCP Server ID
- TEXT/唯一/非空：ToolGroup 名称
- TEXT：ToolGroup 描述
- BOOL/非空：`is_native`，是否为 AuraBot 项目原生 ToolGroup

约束：

- 一个 MCP Server 可以拥有多个 ToolGroup。
- 一个 ToolGroup 只属于一个 MCP Server。
- ToolGroup 名称在 AuraBot 内全局唯一，Task 运行时可以直接使用名称定位授权组。
- AuraBot 自带 MCP Server 下可以存在多个 `is_native = 1` 的 ToolGroup。
- 用户新增的 ToolGroup 使用 `is_native = 0`。
- `is_native` 由 AuraBot 管理，MCP Server 的刷新结果不得修改它。
- 删除 `is_native = 1` 的 ToolGroup 必须由应用层拒绝。
- 非空 ToolGroup 不能直接删除；必须先显式移动或删除其中的 Tool。


## Tool 元信息


- INTEGER/主键/自增：Tool ID
- INTEGER/外键/非空：所属 MCP Server ID
- INTEGER/外键/非空：所属 ToolGroup ID
- TEXT/非空：MCP Server 返回的 Tool 名称；AuraBot 使用同一个名称暴露给 Agent
- TEXT/非空：Tool 功能描述
- TEXT/JSON/非空：MCP Tool 输入 JSON Schema
- TEXT/JSON：MCP Tool 输出 JSON Schema

约束：

- 一个 Tool 只属于一个 ToolGroup。
- Tool 和它所属的 ToolGroup 必须属于同一个 MCP Server。
- MCP Tool 名称只要求在同一个 MCP Server 内唯一，因此使用
  `UNIQUE (server_id, name)`，不要求 `name` 全局唯一。
- AuraBot 不再为 Tool 生成第二个运行时名称。Agent 看到并调用的名称就是 MCP Server
  返回的 `name`。
- 数据库允许不同 MCP Server 存在同名 Tool，但同一个 Task 最终获得授权的 Tool 集合中
  不允许出现重名。Task Manager 在创建 Task 时必须检查名称冲突；存在冲突时不能启动
  Tool 调用，必须先缩小 ToolGroup 授权范围。
- Runtime 根据当前 Task 的授权 ToolGroup，把 Agent 返回的 `name` 唯一解析为
  `server_id + name`，再路由到对应 MCP session 执行 `tools/call`。
- `description`、`input_schema_json` 和 `output_schema_json` 是 MCP Server Tool 定义的
  本地缓存；`group_id` 是 AuraBot 自有配置，刷新 Tool 定义时不得覆盖。
- Agent 只能调用所属 ToolGroup 已由当前 Task 获得授权的 Tool。


## Tool 字段取舍


MCP Tool 可以携带更多可选元信息，但当前 AuraBot 运行时不使用这些信息，因此不写入
`tool` 表：

- `title`：MCP Server 可提供的界面展示标题。它不参与 Tool 定位或调用；当前界面直接
  使用 `name` 和 `description`，因此不保存。
- `annotations`：Server 对只读性、破坏性等行为的提示，不是可靠的权限依据。AuraBot
  的权限由 ToolGroup 和本地运行时规则决定，因此不保存。
- `icons`：只用于界面展示，当前 Tool 管理界面不依赖它，因此不保存。
- `execution`：用于声明 MCP 扩展执行能力，当前 AuraBot 只执行普通 `tools/call`，因此
  不保存。
- `enabled`：本项目使用 ToolGroup 分配运行时权限，不再单独设置 Tool 开关。
- `is_available`：Tool 表保存最近一次成功同步得到的完整 Tool 集合；不再保存额外的
  可用状态。
- `schema_hash`：当前刷新直接更新 Tool 描述和 schema，不做基于哈希的版本审核。
- `last_seen_at`：属于同步诊断信息，当前最小 Tool 模型不保存。


## Tool 发现与刷新规则


每个 MCP Server 独立执行 Tool 发现和刷新：

1. 后端连接目标 MCP Server，并完成 MCP `initialize`。
2. 完整读取该 Server 的所有 `tools/list` 分页。
3. 如果任意请求或分页失败，不修改该 Server 现有的 Tool 缓存。
4. 完整获取并校验成功后，开启一个短 SQLite 事务。
5. 使用 `(server_id, name)` 插入或更新本次返回的 Tool。
6. 更新 MCP 管理的 `description`、`input_schema_json` 和 `output_schema_json`。
7. 保留 AuraBot 管理的 `group_id`。
8. 删除该 `server_id` 下本次成功同步结果中已经不存在的 Tool。
9. 提交事务。

新发现的 Tool 必须先确定所属 ToolGroup：

- 原生 MCP Server 的 Tool 使用项目初始化数据指定的原生 ToolGroup。
- 外部 MCP Server 的 Tool 使用用户注册或管理时指定的非原生 ToolGroup。
- MCP `tools/list` 不负责修改 AuraBot 的 ToolGroup 归属。

刷新过程不得擅自移动 ToolGroup。如果 `tools/list` 失败，必须保留同步前的完整 Tool
集合，不能执行部分更新或删除。


## 建表脚本


```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS mcp_server (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tool_group (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    is_native INTEGER NOT NULL DEFAULT 0
        CHECK (is_native IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (server_id)
        REFERENCES mcp_server(id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    -- 供 tool 的复合外键约束使用，确保 Tool 与 ToolGroup 属于同一个 Server。
    UNIQUE (server_id, id)
);

CREATE INDEX IF NOT EXISTS idx_tool_group_server_id
ON tool_group(server_id);

CREATE TABLE IF NOT EXISTS tool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (server_id)
        REFERENCES mcp_server(id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    FOREIGN KEY (server_id, group_id)
        REFERENCES tool_group(server_id, id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    UNIQUE (server_id, name)
);

CREATE INDEX IF NOT EXISTS idx_tool_server_id
ON tool(server_id);

CREATE INDEX IF NOT EXISTS idx_tool_group_id
ON tool(group_id);
```


## 运行时授权查询


Task Agent 获得一个或多个 ToolGroup 后，可使用以下条件解析实际可调用 Tool：

```sql
SELECT
    t.id,
    t.server_id,
    t.group_id,
    t.name,
    t.description,
    t.input_schema_json,
    t.output_schema_json
FROM tool AS t
JOIN tool_group AS g ON g.id = t.group_id
WHERE t.group_id IN (/* 当前 Task 获得授权的 ToolGroup IDs */)
  AND t.server_id = g.server_id;
```

真正执行 Tool Call 时，运行时仍必须使用当前 Task 的授权 ToolGroup 再检查一次，不能只
依赖系统提示词中展示的 Tool 描述。


## 不在这些表中存储的内容


- MCP Server 的 transport、command、启动参数和认证方式。
- API Key、Token、密码等 secret 明文。
- MCP Server 子进程对象、MCP session 或网络连接对象。
- Tool 背后的 Python、Bash、HTTP 或其他具体实现。
- Tool Call 的运行中状态和完整 stdout/stderr。
- Task Agent 获得的临时 ToolGroup 授权。

Tool Call 状态、结果、错误和产物应由 Task/Event/Artifact 所属模块记录，避免 Tool
注册表成为另一套任务运行时事实来源。
