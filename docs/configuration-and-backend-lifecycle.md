# 配置与 Backend 生命周期

## 配置事实源

| 配置 | Owner | 用途 |
| --- | --- | --- |
| `config.yaml` | Python Backend | ports、Profile 数据根、chat/speech/Web/Memory embedding、Task。 |
| `AURABOT_CONFIG_PATH` | Python Backend | 覆盖 YAML 路径。 |
| Electron Profile SQLite | Electron | Active Profile 与结构化 Soul 的唯一事实源。 |
| `aurabot.config.json` / `config.json` | Electron | 模型、Live2D、通知等桌面设置。 |

`main_agent.profile_id` 已删除。Backend 不读取或创建 `profile.yaml` / `SOUL.md`；Pet 的合法
realtime token 请求提供 `profileId` 和由 Electron `buildSoulPrompt` 生成的有效 Soul。

Profile 运行数据位于：

~~~text
<app.data_dir>/<main_agent.profile_dir>/<profile_id>/
├── context.sqlite3
├── memory.sqlite3
└── qdrant/
~~~

`memory.embedding` 缺省为本地 provider。`chat_model` 同时供 Main Agent invoke 和后台结构化
Memory 提取；缺少 key/地址/model 时请求真实 unavailable，不回退规则提取。

`main_agent.web.tavily_api_key` 为空时不创建 Tavily client，`web_search` / `web_read` 也不会进入
ToolSurface。`main_agent.speech.enabled` 默认为 false；speech 未启用、缺 key、缺本地模型/许可证或
缺 SDK 时 Backend 仍可启动，但 Pet token 请求返回 speech unavailable。

Task 使用 `task` 段；并发固定为 5。旧 `task_runtime`、`task_model`、`local_mcp_server` 和 Electron
taskRunner 字段不再读取。

## 启动与首次激活

~~~mermaid
sequenceDiagram
  participant U as Uvicorn
  participant T as TaskManager
  participant H as RealtimeHub
  participant P as Pet
  participant A as ActiveProfileManager
  participant R as Profile Resources
  U->>T: startup
  U->>H: start accepting tokens
  Note over U,H: 尚无 Runtime / Speech / Context / Memory / Qdrant
  P->>H: token(profileId, soul)
  H->>A: activate
  A->>R: open Context + Memory + model/tools + Speech
  A->>H: bind generation
  H-->>P: one-shot capability
~~~

`GET /health` 始终精确返回 `{"ok":true,"service":"backend"}`，不探测模型、embedding、Qdrant
或 Pi。`GET /api/v1/readiness` 只读 lifespan/realtime admission、active Profile identity 及已打开
Memory 的本地状态。

## Profile 切换与退出

同一 Profile 新 token 只更新 Runtime Soul、递增 generation 并替换 voice session。不同 Profile：

1. 关闭旧 voice session，partial clause 不进入 Context。
2. Runtime cancel invoke/proposal/Observation；Speech cancel delivery/mic。
3. Memory 保留 durable pending row，取消 active-only timer/worker并关闭 SQLite/Qdrant。
4. 关闭 Context、Provider 和 Tavily client。
5. 打开新 Profile 资源并绑定新 generation。

Backend 退出时顺序为 realtime admission → active Profile → TaskManager。已经由 TaskManager 接受的
Task 不随 Profile Runtime 退出；Task 事实按 `owner_profile_id` 在 Profile 再激活后回放。

旧 Main Agent 数据不迁移；开发者应删除旧 Profile 目录后重建。生产 Pi/Node 打包仍以
[Pi Task Runtime](modules/task-agent.md) 为准。
