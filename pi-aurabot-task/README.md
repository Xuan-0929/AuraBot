# @scpz24/pi-aurabot-task

AuraBot 的 Pi 任务扩展，为独立 Pi 子进程提供向 AuraBot Main Agent 提问的
`ask_main_agent` 工具，以及任务执行提示词。本包配合 Pi CLI 使用，任务进程由
AuraBot 的 Python Task Runtime 启动和管理。

## 安装

需要 Node.js `>=22.19.0`。在承载 Pi 的 npm 项目目录中安装：

```bash
npm install --save-exact @earendil-works/pi-coding-agent@0.84.2 @scpz24/pi-aurabot-task@0.1.0 typebox@1.3.7
```

如果已经安装 Pi CLI，也可以登记为 Pi Package：

```bash
pi install npm:@scpz24/pi-aurabot-task@0.1.0
```

## AuraBot 集成

`package.json` 中的 `pi` 字段声明扩展入口和 prompts 目录。AuraBot 通过 Pi RPC
运行任务时，可从安装目录解析以下文件，并分别传给 Pi 的 `--extension` 和
`--append-system-prompt` 参数：

- `node_modules/@scpz24/pi-aurabot-task/aurabot-task/index.ts`
- `node_modules/@scpz24/pi-aurabot-task/prompts/task-system.md`

`ask_main_agent` 接收必填的 `question` 和可选的 `options`。自由文本问题使用
Pi 的 input dialog，带选项的问题使用 select dialog；dialog 标题以
`AURABOT_TASK_QUESTION_V1\n` 开头，供 AuraBot 的 Python RPC 层识别并路由为
TaskQuestion。回答通过工具结果返回，取消或中断返回错误结果。

仅在缺少完成任务所需的业务信息时调用此工具；工具不处理权限审批。使用通用
Pi 客户端时，dialog 由当前客户端处理；自动路由到 Main Agent 需要 AuraBot 的
Python RPC 层配合。源码以 TypeScript 发行，由 Pi 的扩展加载器加载。

## 开发

在本包源码目录执行：

```bash
npm ci
npm run typecheck
npm test
```

许可证：MIT。
