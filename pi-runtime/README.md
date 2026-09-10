# AuraBot Pi Runtime dependency carrier

本目录只锁定并承载 AuraBot Python Task Runtime 启动的 Pi CLI 与仓库内
`@scpz24/pi-aurabot-task` Package。它不是 Node Worker、RPC Client、HTTP 服务或
Task 业务实现。

本目录保留锁定清单；正常源码运行时，从仓库根执行：

```bash
export AURABOT_USER_DATA_DIR="$HOME/AuraBot"
node frontend/scripts/install-runtime.mjs "$AURABOT_USER_DATA_DIR"
```

安装脚本先复制本地扩展及其锁定清单，再串行执行两份 `npm ci`。Pi CLI 位于
`<安装目录>/pi-runtime/node_modules/@earendil-works/pi-coding-agent/dist/cli.js`，
本地扩展位于同一安装目录下的 `pi-aurabot-task/`；不依赖源码目录中的运行时路径。
已有配置的自定义 Pi 路径保留。针对仓库内 Pi 的开发测试仍可执行
`npm ci --prefix pi-aurabot-task` 和 `npm ci --prefix pi-runtime`。
当前未发行应用程序，脚本不打包 Node、Python 或 Electron。
