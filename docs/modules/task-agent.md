# Pi Task Runtime

> 状态：目标架构已确定，执行能力尚未实现。本文是 AuraBot 未来 Task Runtime
> 的唯一事实源；其他文档只描述各自模块如何使用该能力。

## 1. 本轮结论

AuraBot 已终止自研 Task Execution Runtime。旧 ReAct/Plan-and-Solve Harness、
TaskManager、Scheduler、Tool/Skill/Capability registry、Adaptor、MCP gateway、
Agent SQL，以及 Electron 的 Codex/Claude/Hermes runner 均不再属于项目架构。

第一轮结束时：

- Python Backend 只提供 health endpoint。
- Electron 不创建、执行、查询、提醒或展示 Task。
- Task 型输入只作为普通 assistant 消息返回：
  “Task 功能正在迁移，当前暂不可用。”
- 仓库不包含 Pi dependency、Worker、RPC Client、新 Task API 或新 Task UI。

因此，“旧执行面已经删除”不等于“Pi Task Runtime 已经可用”。

## 2. 目标进程模型

~~~mermaid
flowchart TB
  subgraph CORE["Aura Core Process"]
    MAIN["Main Agent"]
    MEMORY["Memory"]
    TM["Python TaskManager<br/>Control Plane"]
    STATE["Task state / persistence / business events"]
    MAIN --> TM
    TM --> STATE
  end

  subgraph INGRESS["Ingress / Media Process"]
    CHANNELS["QQ / WeChat / Telegram"]
    VAD["VAD"]
    STT["STT"]
    GATE["Gate Agent"]
    CHANNELS --> VAD --> STT --> GATE
  end

  subgraph WORKERS["Pi Task Processes"]
    PI1["Task A: Pi RPC"]
    PI2["Task B: Pi RPC"]
    PIN["Task N: Pi RPC"]
  end

  GATE --> MAIN
  TM -->|"stdin/stdout JSONL"| PI1
  TM -->|"stdin/stdout JSONL"| PI2
  TM -->|"stdin/stdout JSONL"| PIN
~~~

第一版只要求一 Task 一 Pi 子进程，不要求立即把 Ingress/Media 从 Core
物理拆出。进程拆分必须服从清晰的数据 owner 与故障边界，不能形成两套业务核心。

## 3. 职责边界

### 3.1 AuraBot Control Plane

未来 Python TaskManager 负责：

- Task 创建、查询、排队、并发准入和用户控制。
- 启动、观察、中断、强制终止和回收 Pi 子进程。
- 产品级 Task 状态、持久化、业务事件、最终结果和 Artifact。
- 将 Task 状态投影给 Main Agent、API 和前端。
- 收口崩溃、非零退出、超时、应用重启和清理失败。

TaskManager 不是 Agent Loop，也不解释 Tool Call，不拥有模型上下文。

### 3.2 Pi Execution Plane

Pi 负责：

- Agent Loop、Provider/模型调用和上下文管理。
- Tool Call 解析与执行。
- Pi 内部重试、压缩、会话上下文和运行时事件。
- Extension、Skill、Prompt 与 Package。

AuraBot 不再实现第二套通用 Agent Execution Framework，也不在 Pi 外复制其
Tool/Skill/Capability 模型。

## 4. RPC 与进程生命周期基线

~~~text
Python TaskManager
→ 解析项目锁定的 Pi CLI
→ 设置 PI_CODING_AGENT_DIR
→ 启动 pi --mode rpc
→ stdin/stdout JSONL
→ 每个 Task 使用一个即用即销毁进程
~~~

- Python 不直接调用 TypeScript SDK。
- 第一版不创建 Node Worker。
- 只有验证 Pi RPC 缺少必要能力后，才可另行评估极薄 Node SDK Worker。
- 用户重跑会创建新执行尝试和新进程；不复用旧进程，不承诺 true resume。

## 5. 安装、Home 与资源协议

- @earendil-works/pi-coding-agent 必须是项目级精确版本依赖。
- 发行包必须携带所需 Node Runtime 与 Pi 依赖，不依赖全局 PATH。
- AuraBot 使用 <userData>/pi-agent/ 作为独立 Pi Home，通过
  PI_CODING_AGENT_DIR 注入。
- 多个 Task 可共享 AuraBot 专属 Pi Home，但不得读取或修改用户
  ~/.pi/agent。
- Tool 只通过标准 Pi Extension 的 pi.registerTool() 提供。
- Skill 只使用标准 SKILL.md。
- Extension、Skill、Prompt 与 Package 只通过 Pi 原生 package/manifest 分发。
- 不创建 PiToolGroup、PiSkillGroup、PiCapabilityManager、
  PiHarnessAdapter 或同义转换层。

### 5.1 已确定的安全边界

- Pi Extension、Skill 与 Package 使用运行 AuraBot 的操作系统用户权限；AuraBot
  不额外发明 Tool 级权限审批、Capability Manager、自研 sandbox、Package 签名
  或私有信任链。
- 用户负责判断资源来源及安装风险。未来安装入口必须清楚展示来源、即将启用的
  资源和失败原因，但这不等于 AuraBot 对第三方代码作安全背书。
- 未来的 Task question/approval 只表示 Task 向 Main Agent 或用户请求业务信息、
  方案选择或继续执行确认，不得被实现为另一套 Tool 权限审批系统。

### 5.2 第二轮必须审批的用户协议

以下内容尚未实现，也不能由第一轮代码暗示已经支持：

- 本地 Extension、Skill 与 Package 的落盘位置。
- 是否支持本地目录、Git、npm 等 Package source，以及各来源的锁定规则。
- 安装、列出、启用、禁用、更新、移除资源的入口与状态模型。
- Task 如何选择 Package，以及是否允许只启用其中部分 Extension 或 Skill。
- 加载错误如何呈现，资源修改何时生效，是否需要重启 Worker。
- AuraBot 内置资源与用户安装资源如何标识、覆盖和升级。

这些协议应在第二轮计划中单独评审；在此之前，AuraBot 没有可用的 Pi 资源安装
或管理功能。

## 6. 产品状态与故障语义

| 场景 | 产品状态 | 进程处理 | Task 级自动重试 |
| --- | --- | --- | --- |
| 正常完成 | completed | 回收进程并提交结果/Artifact | 无 |
| 崩溃或非零退出 | failed | 捕获安全错误并回收 | 无 |
| 超时 | failed（timeout 类别） | 请求停止，grace period 后强杀 | 无 |
| 用户中断 | interrupted | RPC 中断/停止，超时后强杀 | 无 |
| 用户重跑 | 新执行尝试 | 启动全新进程 | 无 |
| 重启发现孤儿 | failed 或提示可重跑 | 不伪装恢复旧进程 | 无 |

Pi 可以执行其内部 model/tool retry；AuraBot 第一版不自动重建整个 Worker。

## 7. 第一轮破坏性清理

~~~mermaid
flowchart LR
  OLD["AuraBot Harness / MCP / CLI runners"] -->|"删除，无兼容层"| GAP["Task 暂不可用"]
  GAP -->|"第二轮重新设计"| FUTURE["Python Control Plane + Pi Execution Plane"]
~~~

第一轮删除：

- Python backend/agent/**、Task/Plan application/gateway/runtime。
- 独立 mcp_server/**。
- Electron Task core、repositories、runner、IPC、UI、设置、提醒与产物桥。
- 四张 Task 表、Task child sessions/messages/links、task.* event logs。
- <userData>/task-inputs 与 <userData>/task-agent-output。

不保留 DTO、read-only API、feature flag、deprecated wrapper、archive 目录或
旧配置映射。历史实现只存在于 Git 历史。

## 8. 第二轮重新设计范围

第二轮必须从本文件的职责边界重新设计，不继承旧类图。届时才设计：

- Python TaskManager、process supervisor、queue、concurrency 和 persistence。
- JSONL RPC command/response/event DTO、背压、超时、中断和崩溃测试。
- 项目级 Pi/Node 锁定、macOS/Windows 打包和 Pi Home 生命周期。
- Pi auth/model/settings、resource profile 与 Package 管理。
- Pi runtime event 到 AuraBot business event 的映射。
- Artifact bridge、Main Agent 接口、新 Task API 与新 Task UI。
- 业务 question/approval、第三方 MCP、true resume 及完整端到端验证。

这些项目在第一轮不得以占位类、空接口或未实现目录提前落地。

## 9. 验收口径

第一轮完成必须同时满足：

1. Backend 仅有 GET /health，返回 {"ok":true,"service":"backend"}。
2. 旧 Task/Plan/MCP 路径和源码目录不存在。
3. Electron Task 输入只产生固定不可用回复且无 Task 副作用。
4. 新数据库不创建 Task 表；旧数据库按受控 migration 清理 Task 数据。
5. 普通聊天、Memory、Live2D、窗口、通知和非 Task 主动能力继续工作。
6. 文档不得宣称 Pi Task Runtime 已实现。
