# 从源码构建与启动

## macOS

环境要求
- git
- node(npm)
- uv

首次安装、构建并启动：

```bash
git clone https://github.com/Xuan-0929/AuraBot.git
cd AuraBot
node frontend/scripts/setup-source.mjs
cd frontend
npm run build
npm run dev
```

安装脚本会自动准备 Python、运行依赖和本地语音模型。等终端显示 `Source setup complete` 后，再继续构建和启动。

以后启动，在 `AuraBot/frontend` 目录执行：

```bash
npm run dev
```

需要调试时，改用以下命令，开启 Live Debug 和诊断日志：

```bash
npm run dev -- --debug
```

## Windows


环境要求
- Git for Windows(会用到git和Git Bash)
- node(npm)
- uv

首次安装、构建并启动：

```powershell
git clone https://github.com/Xuan-0929/AuraBot.git
cd AuraBot
node frontend/scripts/setup-source.mjs
cd frontend
npm.cmd run build
npm.cmd run dev
```

安装脚本会自动准备 Python、运行依赖和本地语音模型。等终端显示 `Source setup complete` 后，再继续构建和启动。
使用 `npm.cmd` 可以避免 PowerShell 拦截 `npm.ps1`。

以后启动，在 `AuraBot/frontend` 目录执行：

```powershell
npm.cmd run dev
```

需要调试时，改用以下命令，开启 Live Debug 和诊断日志：

```powershell
npm.cmd run dev -- --debug
```

缺少Git Bash也能启动，live2d、语音等功能不受影响，但是任务执行不可用。