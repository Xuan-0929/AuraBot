# 配置与 Backend 生命周期

## 配置事实源

Electron Main 创建并维护一个 JSON 文件：默认 `<Electron userData>/desktop-v2/settings.json`。设置 `AURABOT_USER_DATA_DIR` 时直接使用该目录作为数据根，不追加 `desktop-v2`。

~~~text
{
  version: 1,
  revision: 保存版本,
  persona: 结构化人格,
  backend: { app, chat_model, main_agent, memory, task },
  desktop: { pet, currentModelId, systemNotificationsEnabled }
}
~~~

上面是结构示意，不是可复制的完整配置。首次启动会生成符合 schema 的默认值，通过设置页修改即可。Renderer 获取脱敏内容及“是否已配置”标志；密钥输入为空表示保留，清除须显式选择。

| 字段 | 用途 |
| --- | --- |
| `persona` | 名称、人格、语气、关系等；应用时投影到 Electron Profile，再由 Pet 生成有效 Soul。 |
| `backend.chat_model` | Main Agent 与内部 Memory 提炼使用的模型地址、名称、密钥、超时。 |
| `backend.main_agent.speech` | 语音开关、DashScope、STT/TTS 型号及本地模型/许可证路径。 |
| `backend.main_agent.web` | 可选 Tavily 密钥；未配置则不开放 Web 工具。 |
| `backend.app.workspaces` | 已登记的工作区 ID 和绝对路径。 |
| `backend.task` | Pi 模型、Node、默认超时及应用管理的数据/安装路径。 |
| `backend.memory.embedding` | 当前桌面配置使用本地 hash embedding、384 维。 |
| `desktop` | 桌宠显示、模型与桌面偏好。 |

Electron 通过 `AURABOT_CONFIG_PATH` 把 JSON 路径交给 Backend；Python 只在启动时读取 `backend` 段与版本，不写文件、不热加载、不进行环境变量字符串插值。正常桌面启动不读取仓库 `config.yaml`、`config.example.yaml` 或旧 Electron `config.json`。

## 保存与应用

“保存”原子写入 JSON 和新 revision，当前运行不变。“应用”处理已保存版本：Backend 或人格变化需要重启；只有桌面偏好变化时直接应用。启动/重启期间不能并发修改启动配置或再次启动 Backend。

~~~mermaid
sequenceDiagram
  participant S as Settings
  participant E as Electron Main
  participant B as Backend
  S->>E: 保存草稿
  E->>E: 原子写 settings.json
  S->>E: 应用
  E->>B: restart/prepare(force=false)
  alt 忙碌
    B-->>E: ready=false + busy
    E-->>S: 显示中断影响
    S->>E: 用户确认仍然应用
    E->>B: restart/prepare(force=true)
  end
  B->>B: 锁定 admission，阻止新工作
  B-->>E: ready=true
  E->>B: SIGTERM 并等待退出
  E->>B: 启动新实例，传 JSON 路径与实例标识
  E->>B: readiness
  B-->>E: ready + configRevision + instanceId
  E->>E: 匹配后才 markApplied
  E-->>S: 已应用状态
~~~

非强制忙碌检查不得改变运行状态。忙碌包含 queued/running/waiting Task、Memory 实际写入、Main Agent 调用/待提交操作与未完成交付；只有 Memory idle timer 不算写入。旧进程未退出时不启动第二个；新进程失败不标记应用成功，也不自动重启。

## 开发启动与资源激活

从 `frontend` 执行 `npm run dev`：Electron/Vite 启动后由 Electron 独占 Python 子进程。固定 Vite `127.0.0.1:6400`，Backend `127.0.0.1:6401`。不要并行另启 Backend；端口占用时只报告错误。

Backend startup 只创建 Task 控制面、RealtimeHub 和 Profile owner。首个合法 Pet token 提供 `profileId` 与有效 `soul`，才打开该 Profile 的 Context、Memory 和文字 Runtime。连接默认文字；语音资源延迟到 `voice.start`。未配置模型或语音不阻止桌面/设置启动，调用不可用能力会明确拒绝。

`GET /health` 精确返回 `{"ok":true,"service":"backend"}`。`GET /api/v1/readiness` 返回生命周期、实例/配置版本和本地能力状态；不向 Provider 发健康探测。

## 数据布局与退出

~~~text
<数据根>/
├── settings.json                 # Electron：保存设置与人格
├── profile.sqlite3               # Electron：身份和已应用人格投影
├── conversation-history.sqlite3  # Electron：分页的可见交付历史
├── profiles/<profile_id>/
│   ├── context.sqlite3           # Backend：对话 Context
│   ├── memory.sqlite3            # Backend：长期记忆
│   └── qdrant/                   # Backend：向量存储
├── task/
│   ├── task-runtime.sqlite3      # Backend：Task 状态/事件
│   └── sessions/                 # Pi：执行会话
└── pi-agent/                     # Electron 生成 Pi provider 配置
~~~

退出顺序为停止 realtime admission → 关闭活动 Profile 的 Runtime/Speech/Memory/存储 → 关闭 TaskManager/Pi → Electron 关闭自身数据库。Task 的恢复状态由 TaskManager 决定，不自动重跑。

本轮采用全新数据，无旧配置/数据库迁移。已授权的数据重置只删除枚举确认的 AuraBot 专属旧配置和数据库；不删除源码、模型/许可证资产、缓存或共享第三方数据。实际执行记录见 [集成记录](frontend-integration-work.md)。生产 Node/Python/Pi 打包仍需另行完成。
