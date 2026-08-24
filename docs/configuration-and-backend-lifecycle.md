# 配置与 Backend 生命周期

## 当前配置

| 配置 | owner | 用途 |
| --- | --- | --- |
| config.yaml | Python Backend | ports 与仍保留的 Python 模型/Memory 配置。 |
| AURABOT_CONFIG_PATH | Python Backend | 覆盖 YAML 路径。 |
| backend/.python-version | uv | 共享开发 Python 版本。 |
| config.json / aurabot.config.json | Electron | 模型、Live2D、通知等桌面设置。 |

新 `task` 段配置 user data、Pi runtime 载体、Node executable 和默认 timeout。
`task.default_timeout_seconds` 必须在 `1..86400`；缺省为 1800，并且只有 HTTP 请求
显式提供合法 `timeoutSeconds` 时才会覆盖它，显式 null 不等价于省略。
并发固定为 5 且不可配置；旧配置仍含 `task.concurrency_limit` 时启动会明确失败。
旧 task_runtime、task_model、local_mcp_server 和 Electron taskRunner 字段不再读取，
新段不是兼容映射。
已有 JSON 文件不主动重写，未知旧字段被忽略。

## 启动

1. uv 根据 backend/pyproject.toml 与 backend/uv.lock 准备环境。
2. backend.main 加载 YAML。
3. FastAPI lifespan 构造 `TaskManager(settings)`；Manager 初始化同步两表 Store、清理
   tombstone 遗留 session、修复 durable 状态并启动唯一 dispatcher。
4. Uvicorn 暴露固定 GET /health 以及版本化 Task REST/SSE。
5. 开发启动器等待 health 成功，再运行桌面环境。

Backend 不创建旧 Task Runtime、不启动 MCP，也不需要旧 Task model key。Pi 的模型、
认证和资源来自隔离的 `<userData>/pi-agent` Home。

## 退出与失败

- SIGINT/SIGTERM 由 lifespan 在 30 秒总 deadline 内并行回收 Task Worker。
- queued 保留；最多五个 running Task 并行 reap 后变为 `paused(backend_shutdown)`；
  waiting 保持且不持有 Pi。
- 配置缺失、YAML 无效或必需环境变量缺失时启动失败且不回显 secret。
- port 6401 被占用时启动器必须报告诊断，不伪装健康。
- /health 只返回 {"ok":true,"service":"backend"}。

生产 Pi/Node 打包仍未实现；开发期只允许显式配置 Node，并始终使用项目绝对 Pi CLI。
完整 lifecycle 与重启 reconciliation 见 [Pi Task Runtime](modules/task-agent.md)。
