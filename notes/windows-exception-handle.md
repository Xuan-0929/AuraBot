# Windows 异常排查

请在自己的普通 PowerShell 中操作，无需管理员权限。除非另有说明，以下命令均在 **AuraBot 项目根目录** 执行；如果当前位于 `frontend`，先运行 `cd ..`。

## 常见问题

| 问题 | 处理方式 |
|---|---|
| 找不到 `git`、`node` 或 `uv` | 确认工具已安装，然后重新打开 PowerShell。 |
| 安装中途失败 | 解决终端提示的问题后，重新运行 `node frontend/scripts/setup-source.mjs`。已有设置和历史不会被覆盖。 |
| 提示缺少 Git Bash | 安装 Git for Windows 中的 Git Bash，然后重启应用。缺少它时，任务执行不可用。 |
| 提示 `EPERM` 或文件被占用 | 退出正在运行的 AuraBot，再安装或启动。 |
| 端口 6400 / 6401 被占用 | 退出已有 AuraBot 实例，或关闭占用端口的软件。 |
| 提示缺少 MSVC / Windows SDK | 安装 Visual Studio Build Tools，勾选“使用 C++ 的桌面开发”和 Windows SDK，然后重试。 |

如果提示 Pi 缺失、版本错误或检查超时，可在项目根目录先检查，再按提示修复：

```powershell
# 检查任务运行依赖，不下载资源、不调用模型
node frontend/scripts/install-runtime.mjs --check

# 需要补装 Pi 时执行，不重新下载语音模型
node frontend/scripts/install-runtime.mjs --runtime-only
```

自检需要先装好 Python 依赖。若提示缺失，执行：

```powershell
uv sync --project backend --frozen --extra speech --python 3.12
```

## Windows 数据目录

默认数据保存在 `%LOCALAPPDATA%\AuraBot\desktop-v2`，通常无需修改。请在自己的普通 PowerShell 中安装和启动，避免自动化工具把依赖安装到另一份目录。

如需自定义目录，在首次安装前设置：

```powershell
$env:AURABOT_USER_DATA_DIR = Join-Path $env:USERPROFILE 'AuraBotData'
```

以后每次新开 PowerShell，都要在安装、修复或启动前设置相同路径。更换目录不会自动迁移原有设置和角色。
