# Backend Runtime Contract

## 当前契约

Python Backend 当前是 health-only FastAPI 外壳。唯一公开路径：

~~~http
GET /health
~~~

成功响应必须精确为：

~~~json
{"ok":true,"service":"backend"}
~~~

当前无 Task、Plan、MCP、chat、debug 或 registry HTTP API，也无 Task SSE/WebSocket。
未知路径使用 FastAPI 标准 404。

## 配置

Backend 在导入应用时通过 backend.config 读取 YAML：

- 默认路径：仓库根目录 config.yaml。
- 覆盖变量：AURABOT_CONFIG_PATH。
- 字符串支持必填的 ${VARIABLE} 与可选的 ${VARIABLE:-fallback}。
- 缺少配置或必需变量时启动失败，不回显 secret value。

当前 health endpoint 不依赖模型、Task runtime 或 MCP 配置。

## 生命周期

~~~mermaid
sequenceDiagram
  participant Launcher
  participant Uvicorn
  participant Config
  Launcher->>Uvicorn: backend.main:app
  Uvicorn->>Config: initialize YAML
  Uvicorn-->>Launcher: GET /health = ok
  Launcher->>Uvicorn: SIGINT / SIGTERM
  Uvicorn-->>Launcher: normal process exit
~~~

Backend 不创建 BackendRuntime，不绑定 Task event loop，也不负责启动/关闭 MCP。

## 未来扩展规则

新 Main Agent 或 Task API 必须先定义独立版本化契约。未来 Task API 只能投影 Python
Control Plane 的业务状态，不得暴露 Pi stdin/stdout、内部 Tool Call 或旧 Task DTO。
未来约束见 [Pi Task Runtime](modules/task-agent.md)。
