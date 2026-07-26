# AuraBot Backend Runtime 接口

本文档是当前 Python backend HTTP 接口与进程内运行时所有权的唯一准确信息源。
当前 frontend 只是 demo，不是本轮兼容目标。

## 1. 进程与分层

AuraBot 当前分别启动两个本机进程：

```text
backend/main.py (FastAPI, 127.0.0.1:6401)
    → BackendRuntime
    → Application Service
    → Task/ToolGroup/SkillGroup Manager
    → Task/Harness

mcp_server/main.py (MCP Streamable HTTP，通常 127.0.0.1:6410/mcp)
    → FileSystem ToolGroup
    → Plan ToolGroup
    → backend /tool/plan/*
```

backend 不启动或停止 mcp_server；本轮不提供 launcher。

Gateway 只做 HTTP/SSE Body 校验、依赖获取、Application Service 调用和响应编码。
Gateway 不访问 SQLite，也不实现 Task、Plan、Tool 或 Skill 业务规则。

## 2. BackendRuntime

每个 FastAPI application lifespan 构造且只构造一个 `BackendRuntime`。Manager 通过
构造器注入并由该 Runtime 所有，不再使用 `__new__` 进程全局单例。

Runtime 当前持有：

- `task_llm_client`：Task agent 共用的 LLMClient。名字刻意带 `task_`，为未来独立的
  chat-agent LLMClient 留出空间。
- `adaptor_registry`：沿用现有 `create_many(adaptor_names, task)` 协议。
- `tool_group_manager`、`skill_group_manager`：共享能力对象生命周期。
- `harness_factory`：创建 ReAct/Plan-and-Solve Harness。
- `task_manager`：Task 准入、FIFO 队列、Harness runner、打断和保留期。
- `task_service`、`plan_tool_service`：Gateway 使用的 Application Service。

本轮不新增 `AgentContext`。Task 上下文仍由现有 Adaptor 在 ToolCall 进入 Gateway
前注入。

backend shutdown 顺序为：停止准入 → 打断 queued/active Task → 最多等待配置的 grace
period → cancel 未结束 runner → 关闭 SSE → 释放 runtime/capability session →
关闭 Manager → `task_llm_client.aclose()`。

## 3. Tool/Skill 共享能力生命周期

Tool、ToolGroup、Skill、SkillGroup 都不是 Task-specific 实体。运行中的 agent
通过 `CapabilitySession` 持有能力引用：

1. Manager 第一次 acquire 某个 group name 时才从 SQLite loader 或 native factory
   构造对象。
2. 同名能力被多个 agent session 共享，Manager 维护引用计数。
3. 一个 session 对同名 group 最多持有一个引用。
4. session 关闭后引用减一；归零后对象从 Manager cache 销毁。

能力 lease 与完整 retained agent runtime 同寿命。Harness runner 结束时不释放；
手动 clear、100 秒终态保留到期或 backend shutdown 时才释放。

原生 `Plan` ToolGroup 也走同一生命周期。`PlanAndSolveHarness.inject_tool_group()`
通过当前 `CapabilitySession` acquire `Plan`；ReActHarness 当前显式不注入 ToolGroup。

本轮 Manager 只实现 acquire/read/refcount/release/close。已有 SQLite CRUD 继续保留为
内部代码，但不进入生产请求路径；不实现 register/update/delete/refresh 方法或接口。
运行时 cache 已使用 generation wrapper，为未来“新请求读新版本、旧 lease 保持旧版本”
留出结构位置，但本轮不实现 capability mutation。

Skill 当前只向 prompt 提供 group 描述和 Skill 的 name/description，不注入 Skill
正文。本轮没有 Skill MCP Tool；未来应通过同一 CapabilitySession 获取 SkillGroup。

## 4. Task 生命周期

### 4.1 标识、准入与排队

对外唯一标识是 `task_name`：

- Unicode 字符串，长度 1–128；
- 不能有首尾空白；
- 不能包含 `/`、NUL 或控制字符。

backend 生成单调递增的整数 `task_id`，只用于诊断。同名从 admission 到 retained
runtime 被清理之前始终被占用，包括 100 秒终态保留期。

默认最多同时运行 8 个 Harness。超出的 Task 进入 FIFO ready queue，并保持
`initializing`。排队阶段只构造通用 `Task` 和 `TaskStartSpec`，不 acquire ToolGroup、
SkillGroup，也不构造 Harness。

ready queue 协议预留 `initializing + waiting`；本轮只会排入 `initializing`，不实现
Waiting resume。`interrupted/completed/failed` 不留在 ready queue。

排队中的 `initializing` Task 被 interrupt 时立即：

```text
Initializing → Interrupted
→ 从 ready queue 删除
→ 不构造 Harness
→ 开始正常 100 秒保留
```

这不是 deferred interrupt。

timeout 在获得 slot、开始实际 Harness runner 时计时，不包含排队时间。默认
300000 ms，最大 1800000 ms。

### 4.2 激活与结束

激活链路：

```text
CapabilitySession
→ acquire 请求指定的 ToolGroups / SkillGroups
→ 将能力 snapshot 绑定到 Task
→ HarnessFactory.create(Task, CapabilitySession)
→ Harness.inject_tool_group()
→ asyncio.Task(Harness.run())
```

Task 状态 observer 在 `_state_lock` 释放后收到已提交的状态变化并发布 SSE，不能
回滚 Task 状态。

Harness 正常返回值记录在 `result`。异常由 runner 消费，Task 进入 `failed`，对外只
保存固定安全 `error`，不回显异常正文、内部地址、task_name 路由上下文或 secret。

`completed/failed/interrupted` 的完整 Harness runtime 默认保留 100 秒。清理 timer
同时验证 task name 与 record 对象身份，旧 timer 不能误删同名新 runtime。手动 clear
只允许 runner 已结束的终态 Task。

## 5. Task HTTP API

### `POST /task`

创建或排队 Task，成功返回 `202 Accepted`：

```json
{
  "task_name": "Inspect repository",
  "parent_session_id": "main",
  "task_type": "react",
  "prompt": "Inspect the repository and report findings.",
  "workdir": "/absolute/workdir",
  "constraints": "Read only.",
  "skill_groups": ["Research"],
  "tool_groups": ["FileSystem"],
  "timeout_ms": 300000
}
```

`task_type` 仅支持 `react`、`plan_and_solve`。group 数组默认空。未知字段由
Pydantic 拒绝并返回 `422`。

### `GET /task`

返回一个原子快照：

```json
{
  "running_count": 1,
  "queued_count": 1,
  "tasks": []
}
```

### `GET /task/{task_name}`

返回单个 `TaskView`；不存在返回 `task_not_found`。

### `POST /task/{task_name}/interrupt`

打断 active 或 queued Task，并返回最新 `TaskView`。对已结束 Task 重复调用是幂等的。

### `DELETE /task/{task_name}`

手动清理 runner 已结束的终态 runtime，成功返回 `204`。活动 Task 返回
`task_not_terminal`。

### TaskView

```json
{
  "task_id": 1,
  "task_name": "Inspect repository",
  "task_type": "react",
  "state": "thinking",
  "queue_position": null,
  "created_at": "2026-07-26T10:00:00Z",
  "started_at": "2026-07-26T10:00:00Z",
  "settled_at": null,
  "result": null,
  "error": null
}
```

`state` 使用 domain state 的小写 wire value：
`initializing/thinking/pushing/planning/concluding/waiting/completed/failed/interrupted`。

## 6. Task SSE

`GET /task/events` 返回 `text/event-stream`。连接建立后先收到原子
`task.snapshot`，然后收到当前 backend process 内的有序 delta。

```json
{
  "schema_version": 1,
  "stream_id": "process-unique UUID",
  "revision": 42,
  "timestamp": "2026-07-26T10:00:00Z",
  "event": "task.state_changed",
  "running_count": 2,
  "queued_count": 1,
  "payload": {}
}
```

SSE `id` 为 `<stream_id>:<revision>`。事件类型：

- `task.snapshot`
- `task.created`
- `task.queue_changed`
- `task.started`
- `task.state_changed`
- `task.settled`
- `task.removed`
- `runtime.closing`

默认每 15 秒发送 comment keepalive。每个 subscriber 默认有 256 个事件的队列；溢出
时关闭慢订阅者，不静默丢事件。

没有 replay persistence，不处理 `Last-Event-ID` 恢复。重连会收到当前全新 snapshot
和之后的新 delta。

## 7. Plan 内部 Tool API

以下 loopback 路由不出现在公开 OpenAPI：

- `POST /tool/plan/create`
- `POST /tool/plan/read`
- `POST /tool/plan/update`
- `POST /tool/plan/step/complete`

内部 Body 继续为扁平结构：

- create：`task_name + goal + steps`
- read：`task_name`
- update：`task_name + goal + steps`
- complete：`task_name + step_id + completion_summary`

`task_name` 由 `PlanContextAdaptor` 注入标准 MCP `_meta.aurabot.task_name`，LLM
不可见，也不属于 Plan Tool input/output Schema。mcp_server 提取 metadata 后只代理到
上述固定路径。

Gateway 把严格校验后的 Body 转给 `PlanToolApplicationService`。Service 通过
TaskManager 定位 retained Harness，验证 `PlanAndSolveHarness` 后调用 Plan 方法并
映射业务错误。Gateway 自身不维护 Plan。

## 8. Health 与错误

`GET /health`：

```json
{
  "ok": true,
  "service": "backend",
  "accepting_tasks": true,
  "running_count": 2,
  "queued_count": 0
}
```

Application error：

```json
{
  "error": {
    "code": "task_not_found",
    "message": "固定、安全、可行动的说明。"
  }
}
```

非法 HTTP Body 保留 FastAPI `422`。

## 9. `/registry` 占位

`backend/app/gateway/registry_routes.py` 只定义：

```python
router = APIRouter(prefix="/registry", tags=["registry"])
```

它没有任何 endpoint，因此 OpenAPI 当前也没有 `/registry` path。

本轮明确不实现 Registry Application Service、注册方法、CRUD/refresh endpoint、
公开 Adaptor binding 或 Skill Tool Schema。后续必须另行审计协议后再增加。
