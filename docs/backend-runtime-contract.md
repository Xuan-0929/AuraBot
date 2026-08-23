# Backend Runtime Contract

## 当前契约

Python Backend 已在同一 Gateway 内提供版本化 Task REST/SSE，健康路径保持：

~~~http
GET /health
~~~

成功响应必须精确为：

~~~json
{"ok":true,"service":"backend"}
~~~

当前公开 Task 路径为 `/api/v1/tasks`、`/api/v1/tasks/{taskId}` 和
`/api/v1/task-events`；完整方法、错误 envelope、乐观并发与 SSE cursor 以
[Pi Task Runtime](modules/task-agent.md) 为准。旧 `/task`、Plan、MCP、chat、debug
或 registry API 不恢复，未知路径使用 FastAPI 标准 404。

Task response 不暴露 `pendingQuestion`。POST 创建时 `timeoutSeconds` 可省略并由 YAML
默认值填充；显式 null 与范围外数字由 FastAPI 返回 422。Task 的已知 domain error
使用稳定 Task envelope；所有未识别 Backend 异常统一返回脱敏的
`500 internal_error`，message 固定为 `Backend request failed internally.`。

## 配置

Backend 在导入应用时通过 backend.config 读取 YAML：

- 默认路径：仓库根目录 config.yaml。
- 覆盖变量：AURABOT_CONFIG_PATH。
- 字符串支持必填的 ${VARIABLE} 与可选的 ${VARIABLE:-fallback}。
- 缺少配置或必需变量时启动失败，不回显 secret value。

health endpoint 不依赖模型或 Worker 健康状态。新 `task` 段只配置 Pi-native Runtime，
不兼容已删除的 `task_runtime`、`task_model` 或 `local_mcp_server`。

## 生命周期

~~~mermaid
sequenceDiagram
  participant Launcher
  participant Uvicorn
  participant Config
  Launcher->>Uvicorn: backend.main:app
  Uvicorn->>Config: initialize YAML
  Uvicorn->>Config: construct Task Runtime
  Uvicorn-->>Launcher: GET /health = ok
  Launcher->>Uvicorn: SIGINT / SIGTERM
  Uvicorn-->>Launcher: normal process exit
~~~

Backend 不创建旧 BackendRuntime，也不启动 MCP。第二轮由 FastAPI lifespan 启停
TaskManager，并在 shutdown deadline 内并行回收其拥有的 Pi 子进程。

## 边界规则

Task API 只能投影 Python Control Plane 的业务状态，不得暴露 Pi stdin/stdout、
内部 Tool Call、stderr、session 路径或旧 Task DTO。第一版只绑定 loopback，不承诺
远程认证或 CORS；远程开放前必须另行设计威胁模型。
