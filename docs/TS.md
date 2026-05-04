# 技术选型

## 分层架构总览

```
┌─────────────────────────────────────────────────────┐
│               桌面壳层  Electron + React             │
│     Live2D Canvas │ 聊天 UI │ 任务状态 │ 设置面板    │
└────────────────────────┬────────────────────────────┘
                         │ WebSocket (localhost)
┌────────────────────────▼────────────────────────────┐
│               AI 内核  Python + FastAPI              │
│   Deep Agents 编排层（主聊天线程 + 任务子线程）      │
│   记忆系统（ChromaDB + SQLite/NetworkX）             │
│   对话触发系统（轻量判断单元）                      │
└────────────────────────┬────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────┐
│               渠道适配层                             │
│   QQ: NoneBot2 + NapCatQQ                           │
│   微信: WeChatFerry（Windows）/ WeChaty（跨平台）   │
└─────────────────────────────────────────────────────┘
```

---

## 桌面壳层

### 框架：Electron

选择 Electron 而非 Tauri 的原因：
- Live2D Web SDK 依赖完整 Chromium WebGL 环境，Tauri 使用系统 WebView（macOS 为 WKWebView）兼容性不稳定
- Electron 的 `setIgnoreMouseEvents` API 对"桌宠式透明窗口"场景有原生支持
- Node.js 生态可直接管理 Python 子进程（AI 内核）的生命周期

### 前端：React + TypeScript

### Live2D 渲染：PixiJS + pixi-live2d-display

```
pixi-live2d-display  →  PixiJS (WebGL renderer)  →  Electron BrowserWindow
```

### Canvas 交互实现

| 行为 | 实现方案 |
| --- | --- |
| 鼠标悬停 Live2D 后画布透明 | CSS `opacity` 过渡 + mousemove 事件控制 |
| 悬停时画布描边 | CSS `box-shadow` 或 `outline`，hover 时切换 |
| 拖动左下/右下角手柄移动窗口 | 手柄 `mousedown` → `win.setPosition()` via IPC |
| 拖动边框调整大小 | 自定义 resize 手柄 → `win.setSize()` via IPC，PixiJS `app.resize()` 同步缩放 Live2D |
| 桌宠穿透点击 | `win.setIgnoreMouseEvents(true, { forward: true })`，mousemove 命中 Live2D hitbox 时切换为 `false` |

**窗口配置：**
```js
new BrowserWindow({
  transparent: true,
  frame: false,
  alwaysOnTop: true,
  resizable: false,   // 由自定义拖拽控制，禁用系统原生 resize
})
```

---

## AI 内核

### 运行时：Python 3.11+

AI 生态（LangGraph、ChromaDB、NoneBot2 等）均 Python 优先，不走 Node.js 绕路。Electron 在启动时以子进程方式拉起 Python FastAPI 服务。

### 对外接口：FastAPI + WebSocket

- HTTP：配置读写、任务管理、Profile 切换等
- WebSocket：实时聊天消息、打字状态、任务进度推送

### Agent 编排：LangChain

使用 LangChain `create_agent` 构建主聊天 Agent，工具调用、记忆注入、对话历史管理均通过 LangChain 原语实现。任务子线程作为独立的 LangChain Agent 实例运行，与主聊天 Agent 通过消息队列通信。

```
主聊天 Agent（LangChain create_agent）
  ├── 工具：记忆读写、话题管理、任务派发
  └── 对话历史：LangChain Memory / 自定义上下文窗口

任务子 Agent（独立 LangChain Agent 实例）
  ├── 工具：MCP / Skill / 外部 API
  └── 结果回流主聊天 Agent
```

### 记忆系统

| 组件 | 用途 |
| --- | --- |
| **ChromaDB**（本地） | 向量检索，支持语义联想召回 |
| **SQLite** | 结构化存储 MemoryNode / MemoryEdge，图遍历用递归 CTE 或简单 Python 实现 |

ChromaDB 和 SQLite 职责不重叠：ChromaDB 解决"语义上像什么"，SQLite 解决"沿关系边跳转到哪"。Phase 1 不引入 NetworkX，SQLite 足够支撑图查询；图结构复杂到 SQL 写起来痛时再考虑引入图库。

### 对话触发系统

独立轻量判断单元，使用小模型（如 GPT-4o-mini 或本地 Qwen）处理：
- 用户是否说完
- 当前是否应该开口
- 当前输入：聊天 or 任务
- 当前对话段是否结束（触发记忆整理）

与主回复模型完全解耦，降低成本与上下文污染。

---

## 渠道适配层

QQ 和微信均使用各自原生接入方式。架构上预留 `ChannelAdapter` 基类，渠道实现与 AI 内核解耦。

---

## 依赖总览

### Electron 侧（Node.js）

| 包 | 用途 |
| --- | --- |
| `electron` | 桌面壳层 |
| `react` + `typescript` | 前端 UI |
| `pixi.js` | WebGL 渲染引擎 |
| `pixi-live2d-display` | Live2D 模型加载与渲染 |
| `vite` + `electron-vite` | 构建工具 |

### Python 侧

| 包 | 用途 |
| --- | --- |
| `fastapi` + `uvicorn` | HTTP + WebSocket 服务 |
| `langchain` + `langchain-openai` | Agent 编排 + LLM 调用 |
| `chromadb` | 向量存储与语义召回 |
| `apscheduler` | 定时任务调度 |

---

## 待决策事项

1. Live2D 模型格式确认（Cubism 3 / Cubism 4），影响 SDK 版本选择
2. Python 子进程打包方式：PyInstaller 内嵌 vs 要求用户本机 Python 环境
3. 主模型选型（OpenAI API / 本地模型 / 混合），影响延迟与成本策略
