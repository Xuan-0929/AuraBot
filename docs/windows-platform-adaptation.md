# AuraBot Windows Platform Adaptation

> 状态：保留 Windows 10/11 x64 打包目标，尚需实机回归。

## 当前范围

- Electron userData 是 Windows 配置与 SQLite 的平台路径来源。
- better-sqlite3、Live2D、透明窗口和通知保持跨平台实现。
- package:win 与 package:mac 构建入口保留。
- 不再发现 Codex、Claude Code 或 Hermes CLI；旧 runner 已删除。

## Pi 第二轮要求

~~~mermaid
flowchart LR
  PACKAGE["AuraBot installer"] --> NODE["bundled Node runtime"]
  PACKAGE --> PI["project-pinned Pi"]
  APPDATA["Electron userData"] --> HOME["pi-agent home"]
  NODE --> PI
  HOME --> PI
~~~

未来 Windows 发行必须携带 Node 与项目锁定 Pi，并把 PI_CODING_AGENT_DIR 指向
AuraBot 专属 userData/pi-agent。不得依赖 PATH、全局 npm 或用户 ~/.pi/agent。

该打包能力尚未实现；第一轮不得新增占位 Worker 或 CLI discovery。

## 回归清单

- npm run typecheck
- npm run test:unit
- npm run build
- package:mac 在 macOS 完成 smoke
- package:win 在 Windows 完成安装与启动 smoke
- Live2D、窗口、配置文件、SQLite 与非 Task 通知正常
