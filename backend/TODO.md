# 直到实现 ReAct Loop 还差哪些

## 当前进度

```text
[完成] Runtime schema 单一来源
[完成] MCP Server SQL CRUD
[完成] ToolGroup SQL CRUD
[完成] Tool SQL CRUD 与查询
[延后] MCP Tool 原子全量同步
[部分] Tool domain model
[未做] MCP client/session
[未做] ToolGroup 权限解析
[未做] Tool Call Gateway
[未做] Harness Tool Call 解析协议
[未做] 合法 Harness 初始化协议
[未做] ReAct loop
```

当前位于“工具注册与持久化层”。MCP Server、ToolGroup 和 Tool 的单条 CRUD 已经
具备，但 MCP 连接和整个 Tool 执行链尚未完成，因此还不能开始可运行的 ReAct Harness。


## 1. Runtime schema 单一来源

- [x] 在 `backend/agent/integration/sql/utils.py` 集中保存全部建表语句。
- [x] 包含 `skill_group`、`skill`、`mcp_server`、`tool_group`、`tool` 和索引。
- [x] `ensure_schema()` 负责幂等建表、外键检查、事务提交和失败回滚。
- [x] `create_connection()` 统一设置 `row_factory`、外键约束和超时。
- [x] `initialize_schema()` 是运行 `ensure_schema()` 的唯一运行入口。
- [x] `read_connection()` 管理一次完整逻辑读取的连接生命周期。
- [x] `transaction()` 统一管理写事务的提交、回滚和连接关闭。
- [x] 运行时 CRUD 不再执行 `ensure_schema()`。
- [x] SkillGroup/ToolGroup 使用单连接 JOIN 加载子对象，不再产生嵌套连接。
- [x] `scripts/AgentSQLiteInitialize.py` 只处理路径、CLI 和连接，调用统一
  `ensure_schema()`。
- [x] 覆盖新数据库初始化、旧数据库补表、重复执行和数据保留测试。


## 2. MCP Server SQL CRUD

新建 `backend/agent/integration/sql/MCPServerSQL.py`，直接与 SQLite 交互。

- [x] `insert_mcp_server_metadata(name, url, description=None)`。
- [x] `delete_mcp_server_metadata(url)`。
- [x] `update_mcp_server_metadata(url, *, new_name=None, new_url=None,
  description=None)`。
- [x] `get_mcp_server(url)`。
- [x] `get_mcp_server_by_id(server_id)`。
- [x] `get_mcp_server_by_name(name)`。
- [x] `list_mcp_servers()`。
- [x] 名称和 URL 唯一冲突时返回失败，不覆盖旧记录。
- [x] Server 下仍有 ToolGroup 时拒绝删除。
- [x] URL 中不得保存 Token、API Key、用户名或密码。
- [x] 增加真实临时 SQLite CRUD 测试。

完成标准：注册外部 MCP Server 不再需要调用方直接写 SQL。


## 3. ToolGroup SQL CRUD

- [x] 根据 `server_url` 查询 `mcp_server.id` 后插入 ToolGroup。
- [x] 插入、删除、更新、按名称读取、按 ID 读取。
- [x] 查询全部 ToolGroup。
- [x] 按 MCP Server URL 查询 ToolGroup。
- [x] `include_tools=True` 时构造包含 `list[Tool]` 的运行时 ToolGroup。
- [x] 原生 ToolGroup 删除保护。
- [x] 非空 ToolGroup 删除保护。
- [x] ToolGroup 名称全局唯一。
- [x] 允许合法空 ToolGroup 在运行时重建为 `tools=[]`。
- [x] 增加 CRUD、删除保护和 Server 归属测试。


## 4. Tool SQL CRUD 与查询

### 已完成：单条 CRUD 与查询

- [x] 插入、删除、更新 Tool。
- [x] 使用 `(server_url, name)` 唯一定位运行时 Tool。
- [x] 按数据库 ID 查询 Tool，但不把 ID写入运行时对象。
- [x] 按 ToolGroup、名称和 MCP Server URL 查询 Tool。
- [x] JSON Schema `dict` 与 SQLite JSON 字符串互转。
- [x] 同一 Server 内 Tool 名称唯一。
- [x] 不同 Server 允许同名 Tool。
- [x] Tool 和 ToolGroup 必须属于同一个 MCP Server。
- [x] Tool 只允许移动到同 Server 的其他 ToolGroup。
- [x] `output_schema=None` 可以显式清空 SQL 字段；未传则保留旧值。

### 延后：MCP Tool 原子全量同步

当前是低并发、单机本地项目。现有单条 CRUD 足够支撑 Tool 持久化。以下能力不作为
当前阶段缺口，也不阻塞 Agent Harness；只有增加后台刷新、多写入者或严格远端快照要求
时再实现。

- [ ] 定义完整 Tool 发现结果的数据结构。
- [ ] 实现指定 Server 的完整 `tools/list` 分页读取。
- [ ] 任意分页失败时不修改现有 Tool 缓存。
- [ ] 完整发现成功后才开启 SQLite 写事务。
- [ ] 按 `(server_id, name)` 插入新 Tool 或更新已有 Tool。
- [ ] 更新 MCP 管理的 `description`、`input_schema_json`、
  `output_schema_json`。
- [ ] 刷新时保留 AuraBot 管理的 `group_id`。
- [ ] 删除该 Server 下已经不在完整发现结果中的 Tool。
- [ ] 同步异常时回滚整批修改。
- [ ] 增加新增、更新、删除、分页失败、事务回滚测试。

完成标准：一次同步后，数据库中某 Server 的 Tool 集合与该 Server 的完整
`tools/list` 结果一致；失败时数据库保持同步前状态。


## 5. Tool domain model

当前 `Tool` 已包含：

```python
name: str
description: str
server_url: str
input_schema: dict
output_schema: dict | None
```

当前 `ToolGroup` 已持有：

```python
tools: list[Tool]
```

已完成：

- [x] 给运行时 Tool 增加 `input_schema: dict`。
- [x] 给运行时 Tool 增加 `output_schema: dict | None`。
- [x] SQL 查询从 JSON 字段恢复 schema 并构造完整 Tool。
- [x] `load_intro()` 使用真实 input/output schema，删除硬编码的 `query: string`。
- [x] schema JSON/XML 的序列化、解析和格式化集中在
  `backend/agent/tools/utils/formatter.py`。
- [x] 明确 Tool 对象只描述和路由能力，不直接持有数据库 ID、Group ID、认证信息或
  MCP session。
- [x] 增加 schema、默认本地 Server URL、显式外部 Server URL 测试。

仍需考证：

- [ ] 结合 Harness 的 `parse_tool_call()` 调用协议明确 `Tool.execute()` 的职责；当前
  保持现有方法不变。


## 6. MCP client/session

- [ ] 选择并接入 MCP Python SDK。
- [ ] 根据 `server_url` 建立 MCP ClientSession。
- [ ] 支持 MCP `initialize` 生命周期。
- [ ] 支持 `tools/list` 及分页。
- [ ] 支持 `tools/call`。
- [ ] 同一 Server 的并发调用复用 session 或受控连接池。
- [ ] 连接失败后清理失效 session，并定义安全重连行为。
- [ ] 应用关闭时关闭全部 session。
- [ ] 为超时、协议错误、Server 返回错误定义统一异常。
- [ ] 使用 fake MCP Server 测试初始化、发现、调用、断线和关闭。

完成标准：给定已注册 `server_url`，后端可以独立完成 `initialize`、`tools/list` 和
`tools/call`。


## 7. ToolGroup 权限解析

完善 `ToolGroupManager` 或新增独立权限解析器。

- [ ] 根据 Task 获得授权的 ToolGroup 加载实际 Tool。
- [ ] 展开多个 ToolGroup，生成当前 Task 唯一的可调用 Tool 集合。
- [ ] 检查不同 Server 的同名 Tool 冲突。
- [ ] 发现重名时拒绝启动 Task，不静默覆盖、不自动改写 Tool 名称。
- [ ] 建立 `tool_name -> Tool(server_url, ...)` 路由索引。
- [ ] Tool Call 前再次校验 Tool 是否仍属于授权 ToolGroup。
- [ ] 未授权 Tool、未知 Tool、重名 Tool 返回不同错误。
- [ ] 权限解析只存在于 Task 运行时，不写入 Tool 注册表。
- [ ] 增加单组、多组、空组、重名、未授权调用测试。


## 8. Tool Call Gateway

新增统一 Tool 执行入口，Harness 不直接操作 MCP session。

- [ ] 接收 `tool_name`、`arguments` 和当前 Task 授权上下文。
- [ ] 检查 Tool 名称存在且唯一。
- [ ] 检查 Tool 已授权。
- [ ] 使用 Tool 的 `input_schema` 校验 arguments。
- [ ] 根据 `server_url` 取得或建立 MCP session。
- [ ] 调用 MCP `tools/call`。
- [ ] 标准化文本、结构化内容、错误和超时结果。
- [ ] 不把异常 traceback、Token 或内部连接信息返回给 LLM。
- [ ] 记录足够的 Task/Event 调试信息，但不在 Tool 表保存调用状态。
- [ ] 增加成功、参数错误、未授权、未知 Tool、Server 超时、Server 错误测试。

建议接口：

```python
await tool_gateway.call(
    tool_name=tool_name,
    arguments=arguments,
    authorization=task_tool_authorization,
)
```


## 9. Harness Tool Call 解析协议

Tool 不注册进 `LLMClient`。Tool 定义由 Harness 写入系统提示词，LLM 返回普通文本，
ReAct Harness 在每轮调用 `parse_tool_call()` 判断下一步动作。

- [ ] 定义可区分最终回答与 Tool Call 的稳定文本协议。
- [ ] 每轮 LLM 响应后调用一次 `parse_tool_call()`。
- [ ] 从 Tool Call 中解析 `tool_name` 和 JSON object `arguments`。
- [ ] 明确单轮允许的 Tool Call 数量和多个调用的处理方式。
- [ ] 非法格式或非法 JSON arguments 返回可恢复的解析错误，不让 loop 崩溃。
- [ ] Tool Call 解析结果交给权限解析器和 Tool Call Gateway，不直接操作 MCP session。
- [ ] 明确 Tool observation 写回 messages 的格式和 role。
- [ ] `LLMClient` 保持普通消息请求，不增加原生 `tools`、`tool_choice` 或
  `tool_calls` 协议。
- [ ] 增加最终文本、合法调用、未知 Tool、非法 JSON、多调用和解析恢复测试。


## 10. 合法 Harness 初始化协议

当前 `Harness` 使用非法的 `async def __init__`，必须修复后才能运行任何 Harness。

- [ ] 把 `Harness.__init__` 改为同步构造函数。
- [ ] `task`、`llm_client` property 改为同步 property。
- [ ] 将异步 prompt 初始化移入 `async initialize()` 或异步工厂。
- [ ] 推荐提供：

```python
@classmethod
async def create(cls, *, task, llm_client, ...):
    harness = cls(task=task, llm_client=llm_client, ...)
    await harness.initialize()
    return harness
```

- [ ] 保证 system/user 初始消息只写入一次。
- [ ] 明确 Harness 状态转换：Initializing → Pending → Running → Completed/Failed。
- [ ] 初始化失败时 Task 状态和异常可观察。
- [ ] 修复现有 `TypeError: __init__() should return None, not 'coroutine'`。
- [ ] 恢复 Harness prompt 测试。


## 11. ReAct loop

- [ ] 获取 Task 当前授权 Tool 集合。
- [ ] 将授权 Tool 的真实 schema 描述写入 system prompt。
- [ ] 调用 LLM。
- [ ] 每轮响应调用 `parse_tool_call()`，区分最终回答、Tool Call 和解析错误。
- [ ] 最终文本响应结束 loop并保存结果。
- [ ] 保存触发 Tool Call 的原始 assistant message。
- [ ] 通过 Tool Call Gateway 执行解析出的调用。
- [ ] 按已确定的 observation 协议写回执行结果。
- [ ] 再次调用 LLM，直到最终文本响应。
- [ ] 设置最大轮数，达到上限时明确失败或进入 concluding。
- [ ] 支持 Task 取消和中断。
- [ ] 区分可恢复 Tool 错误与不可恢复系统错误。
- [ ] 确保所有异常最终映射到 Task 状态。
- [ ] 保存最终 assistant message 和 Task 输出。
- [ ] 增加纯文本完成、单轮 Tool、多轮 Tool、Tool 错误、未授权、重名、轮数上限、
  取消测试。


## 推荐执行顺序

```text
补全 Tool domain schema
→ MCP client/session
→ ToolGroup 权限解析
→ Tool Call Gateway
→ Harness Tool Call 解析协议
→ 修复 Harness 初始化协议
→ ReAct loop
```


## 进入 Harness 开发前的门槛

开始实现 Harness Tool Loop 前，以下条件必须全部满足：

- [x] MCP Server 能通过正式 SQL API 注册、查询、更新和删除。
- [ ] MCP Client 能完成 `initialize`、`tools/list` 和 `tools/call`。
- [ ] MCP `tools/list` 结果能通过现有 ToolSQL CRUD 持久化。
- [x] Runtime Tool 包含真实 input/output schema 和 `server_url`。
- [ ] Task 授权 ToolGroup 能解析成无重名的 Tool 集合。
- [ ] Tool Call Gateway 能执行授权校验、参数校验和 MCP 调用。
- [ ] Harness system prompt 能描述授权 Tool，且 `parse_tool_call()` 协议已确定并有测试。
- [ ] Harness 构造和异步初始化协议合法。

这些门槛全部完成后，再实现 ReAct loop，避免 Harness 同时承担注册、同步、权限、连接和
协议适配职责。
