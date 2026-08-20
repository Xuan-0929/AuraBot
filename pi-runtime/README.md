# AuraBot Pi Runtime dependency carrier

本目录只锁定并承载 AuraBot Python Task Runtime 启动的 Pi CLI 与仓库内
`@scpz24/pi-aurabot-task` Package。它不是 Node Worker、RPC Client、HTTP 服务或
Task 业务实现。

开发时执行 `npm ci --prefix pi-runtime`。Python 通过配置解析这里的绝对
`node_modules/@earendil-works/pi-coding-agent/dist/cli.js`，不调用全局 `pi` 或
`npx`。生产 Node/Pi 复制与签名仍属于后续发行工作。
