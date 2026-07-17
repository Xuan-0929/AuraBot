# 配置与后端生命周期

## 目标

让 AuraBot 的开发和桌面运行时明确知道：使用哪个 Python、读取哪份配置、后端何时
启动、何时可用，以及失败后如何向用户和开发者反馈。本文覆盖迁移路线的 Phase 0，
不改变 Main Agent、记忆或任务的业务所有权。

## 当前事实与目标边界

当前 `frontend/scripts/dev.mjs` 同时启动 Uvicorn 和 Electron，Python 解释器按
`PYTHON`、`AURABOT_PYTHON`、`python3` 的顺序解析。`backend.main` 启动时读取根
目录 `config.yaml`；Electron TypeScript core 仍使用 userData 目录下的 JSON 配置。

Phase 0 不要求立即合并两种配置格式。它要求所有调用方显式知道自己读取哪份配置，
并且 Python 后端不再依赖开发者猜测 shell PATH。

## 配置所有权

| 配置 | 当前 owner | 用途 | 规则 |
| --- | --- | --- | --- |
| `config.yaml` | Python runtime | 网关端口、Python 模型与后端模块配置 | 由 `backend.config` 启动时一次性加载。 |
| `AURABOT_CONFIG_PATH` | Python runtime | 覆盖 YAML 路径 | 测试和部署使用；相对路径相对仓库根目录解析。 |
| `PYTHON` / `AURABOT_PYTHON` | 开发启动器 | 选择 Uvicorn 解释器 | 必须是可执行文件的绝对路径。 |
| `aurabot.config.json` / `config.json` | Electron legacy runtime | 现有 TypeScript 模型、任务和 UI 设置 | 只由 Electron 读取，不能被 Python 隐式读取。 |

密钥只能通过环境变量或受控的本地配置读取。日志、健康接口和错误提示不得回显密钥。

## 启动协议

### 开发模式

1. 启动器解析 Python 解释器并验证 `python -m uvicorn --version` 可执行。
2. 启动器启动 `backend.main:app`，等待 `/health` 返回 `ok: true`。
3. 后端健康后启动 Electron / Vite。
4. Electron 通过 `/health` 和 `/services` 记录网关状态；业务页面只使用已声明可用的后端能力。

当前 `npm run dev` 已并行启动进程；Phase 0 实现应将“后端已可连接”变为明确条件，
而不是仅依赖子进程存在。

### 退出模式

1. Electron 请求退出或开发启动器收到 `SIGINT` / `SIGTERM` 时，先停止接受新后端请求。
2. 向 Main Agent 和 task-manager 广播 shutdown，给受控任务一个短暂清理窗口。
3. 停止 Uvicorn；未完成任务按其恢复策略持久化状态。
4. 不在退出时删除用户数据、任务产物或记忆数据库。

## 失败策略

| 场景 | 启动器行为 | 用户可见状态 |
| --- | --- | --- |
| Python 不存在或缺少 `uvicorn` | 不启动 Electron 业务运行时；输出诊断命令 | “后端环境未准备好”，显示解释器路径与依赖安装提示。 |
| `config.yaml` 缺失 | 后端启动失败，不尝试写默认密钥配置 | “缺少后端配置”，提示从示例配置创建本地文件。 |
| 必需环境变量缺失 | 后端启动失败，不显示变量值 | “缺少模型配置”，显示缺失变量名。 |
| 端口 `6401` 被占用 | 探测现有服务；仅当其 `/health` 识别为 AuraBot 网关时复用 | “后端端口被占用”，提供停止或改端口的恢复建议。 |
| 后端运行中退出 | Electron 保持窗口可见但禁用已迁移的后端操作 | “后端已断开”，提供重试与诊断入口。 |

## 日志与健康

- 开发模式：启动器可将后端和 Electron 标准输出写入各自日志流，保留时间、进程名和退出码。
- 生产模式：后端日志写入 userData 下的受控日志目录；避免写到仓库目录。
- `/health` 只报告运行状态、版本和依赖健康摘要；`/services` 只报告已注册服务。
- 业务诊断需要结构化 error code，格式以[运行时契约](backend-runtime-contract.md)为准。

## 验收标准

1. 显式 `PYTHON` 指向正确环境时，`npm run dev` 能启动 Python、Vite 与 Electron。
2. 缺少 `uvicorn`、YAML 配置或必需环境变量时，失败消息能指向恢复步骤且不泄露密钥。
3. 后端健康前，Electron 不发送已迁移的业务请求。
4. 后端异常退出后，Electron 能展示 offline 状态且不制造新的本地业务写入。
5. 正常退出不会留下孤立的 Uvicorn 进程，也不会删除用户数据。
