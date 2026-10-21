# 从源码安装与启动

## 准备工具

先安装以下工具：

- **Git**：Windows请安装包含Git Bash的Git for Windows。
- **Node.js 22.19 或更高版本**，包含npm。
- **uv**：安装脚本会自动准备Python和项目依赖。

## 首次安装

### macOS

在终端执行：

```bash
git clone https://github.com/Xuan-0929/AuraBot.git
cd AuraBot
node frontend/scripts/setup-source.mjs
```

### Windows

在 PowerShell 执行：

```powershell
git clone https://github.com/Xuan-0929/AuraBot.git
cd AuraBot
node frontend/scripts/setup-source.mjs
```

安装脚本会准备运行依赖并下载本地语音模型，首次安装可能需要一些时间。**看到 `Source setup complete` 后，进入前端目录：**

```bash
cd frontend
```

然后启动应用：

| 系统 | 启动命令 |
|---|---|
| macOS | `npm run dev` |
| Windows | `npm.cmd run dev` |

启动命令会自动构建并打开控制台，无需提前运行 `build`。

## 日常启动与更新

以后启动时，进入 **AuraBot/frontend** 目录，执行：

| 操作 | macOS | Windows |
|---|---|---|
| 启动 | `npm run dev` | `npm.cmd run dev` |
| 开启调试日志 | `npm run dev -- --debug` | `npm.cmd run dev -- --debug` |

更新前先退出 AuraBot，回到 **AuraBot 项目根目录**，再执行：

```bash
git pull --ff-only
node frontend/scripts/setup-source.mjs
```

等待安装完成，再执行 `cd frontend`，使用上表中的命令启动。

Windows 安装或启动遇到问题时，请查看 [Windows 异常排查](windows-exception-handle.md)。
