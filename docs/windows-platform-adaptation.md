# AuraBot Windows Platform Adaptation

> 状态：保留 Windows 10/11 x64 打包目标，尚需实机回归。

## 当前范围

- 默认数据根为 Electron userData 下的 `desktop-v2`，由其中 `settings.json` 统一保存配置；Python 路径由 Electron 显式传入。
- better-sqlite3、Live2D、透明窗口和通知保持跨平台实现。
- package:win 与 package:mac 构建入口保留。
- 不再发现 Codex、Claude Code 或 Hermes CLI；旧 runner 已删除。

## Pi 第二轮后端范围

~~~mermaid
flowchart LR
  PACKAGE["AuraBot installer"] --> NODE["bundled Node runtime"]
  PACKAGE --> PI["project-pinned Pi"]
  APPDATA["Electron userData"] --> HOME["pi-agent home"]
  NODE --> PI
  HOME --> PI
~~~

Backend 使用 Windows 进程组/`taskkill /T /F` 的终止边界，把 `PI_CODING_AGENT_DIR` 指向 AuraBot 数据根下的 `pi-agent`。开发环境使用设置中的 Node 可执行文件与仓库 pinned Pi；生产包应携带运行时，不依赖全局 Pi 或用户 `~/.pi/agent`。

生产 Windows 安装包携带 Node/Python/Pi 仍未实现；`pi-runtime` 只是依赖载体，不能
被描述为 Node Worker 或发行闭环。

## 回归清单

- npm run typecheck
- npm run test:unit
- npm run build
- package:mac 在 macOS 完成 smoke
- package:win 在 Windows 完成安装与启动 smoke
- Live2D、窗口、JSON 保存/应用、SQLite 历史、只读 Task 与显式语音启停正常
