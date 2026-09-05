# AuraBot 当前开发状态

更新：2026-09-05。本文描述代码集成范围；本轮执行的测试、桌面检查与剩余限制由 [集成记录](frontend-integration-work.md) 维护。

| 范围 | 当前集成 |
| --- | --- |
| Desktop | Electron + Vue；保留 Live2D 桌宠、控制台、设置和窗口交互。 |
| 配置 | 单个 Electron JSON；模型、语音、人格、工作目录使用保存/应用；重启先做忙碌确认。 |
| Backend | Electron 独占一个 Python 子进程；启动无热加载，无自动重启；健康确认后才标记应用版本。 |
| 文字 | Console → IPC → Pet 唯一 WebSocket → Main Agent；不依赖语音资产。 |
| 语音 | 显式开麦；STT partial 内部使用，确认后才进入可见历史；完整输出、持久化、展示后 ACK。 |
| 可见历史 | Electron 独立 SQLite，稳定 ID 去重和游标分页；只含接受的用户消息与已交付 `say_to_user`。 |
| Memory | Backend 内部召回/归档；已封存批次独立提炼；外部 API 与管理页移除。 |
| Task | Python TaskManager + Pi；Main Agent 工具管理生命周期，Console 只显示标题和状态。 |
| 数据 | 全新运行数据，不迁移旧配置/数据库；保留资产、缓存和共享数据。 |

## 仍须区分的边界

- 自动测试和模拟 transport 不代表真实麦克风、音频输出设备、STT/TTS 或模型服务已通过验收。未执行的真实检查必须明确标注。
- Readiness 表示本地进程与配置状态，不代表 Provider 凭据或网络已通过真实调用。
- Windows 实机与生产 Node/Python/Pi 打包仍需独立完成；保留构建入口不等于发行闭环。
- 本轮没有 Artifact 管理、Task 手动控制 UI、外部消息渠道或旧 Electron Memory 主动提醒服务。

## 开发验证入口

~~~bash
uv run --project backend python -m unittest discover -s backend/tests -t .
cd frontend
npm run typecheck
npm test
npm run build
~~~

`npm test` 使用 Node 版本的 native SQLite；桌面启动需要 Electron ABI，`npm run dev` 会先重建对应 native 模块。文档变更按项目约定无需额外验证；具体测试结果仅记在集成记录。
