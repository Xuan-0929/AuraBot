# AIRI UI 迁移接入接口文档

## 1. 文档目标

本文档用于指导把 GitHub 上 `moeru-ai/airi` 项目的 UI 界面迁移到 AuraBot 现有框架中。

这里的“搬 UI”不是把 AIRI 整个前端和运行时一股脑复制进 AuraBot，而是把它的：

1. 视觉风格
2. 布局结构
3. 交互动效
4. 场景级页面壳层

接到 AuraBot 已有的：

1. Electron 窗口体系
2. React 渲染层
3. IPC 能力桥
4. 聊天 / 任务 / 设置 / Live2D / 通知 / 记忆等业务状态

之上。

一句话结论：

**AIRI 属于“整套独立前端工程接入”类型，应按壳层迁移 + 状态适配 + 桌面能力桥接的方案接入，而不是按静态页面复制或 React 组件直接复用的方案处理。**

## 2. 对 AIRI 仓库的判断

### 2.1 结论

`AIRI` 不是以下两种：

1. 不是一组现成 React 组件
2. 不是一套简单静态 HTML/CSS 页面

它属于第三种：

**一整套独立前端工程。**

### 2.2 判断依据

从仓库结构看，AIRI 具有以下特征：

1. 根目录是 `pnpm workspace + turbo` 的 monorepo。
2. 存在多个应用入口，包括 `apps/stage-web`、`apps/stage-tamagotchi`、`apps/stage-pocket`。
3. 存在共享 UI 包，包括 `packages/stage-ui`、`packages/stage-ui-live2d`、`packages/stage-layouts`、`packages/stage-pages`。
4. 前端技术栈是 `Vue 3 + Vite + electron-vite + UnoCSS + Pinia + vue-router`。
5. 桌面端舞台能力、Live2D、场景页、共享状态和动效都已经被拆成独立 package。

因此它的 UI 并不是“拷几个页面就能跑”，而是依赖一整套：

1. Vue 组件树
2. 路由与 store
3. 共享样式系统
4. Live2D / Electron 场景依赖
5. 场景级状态编排

## 3. 目标系统边界

当前 AuraBot 的真实承接框架是：

1. `Electron + React + TypeScript`
2. 渲染层主界面已经拆成 `DesktopPet`、`ConsoleWindow`、`SettingsWindow`
3. 能力桥已经通过 `AuraBotApi` 和 IPC channels 暴露
4. 共享业务契约已经沉淀在 `src/shared/contracts/*` 与 `src/shared/schemas/*`

当前可以直接承接 UI 迁移的核心能力有：

1. 聊天消息流
2. Agent 状态
3. 任务列表与任务控制
4. Live2D 模型状态
5. 设置中心配置
6. 桌宠行为设置
7. 通知设置
8. 记忆调试快照

这意味着我们迁移 AIRI UI 时，**目标不是替换 AuraBot 的业务核心，而是替换和升级展示层。**

## 4. 迁移总原则

### 4.1 只搬 UI，不搬 AIRI 业务内核

AuraBot 继续保留自己的：

1. 聊天编排
2. 任务系统
3. 记忆系统
4. 设置存储
5. Electron 窗口生命周期
6. IPC 通信模型

迁移的对象仅限于：

1. 视觉壳层
2. 布局结构
3. 组件外观
4. 交互反馈
5. 页面级信息组织方式

### 4.2 不直接把 AIRI 的 Vue 运行时硬塞进 AuraBot 的 React 主树

不推荐的做法：

1. 在 AuraBot 的 React 页面里直接混挂 AIRI 的 Vue 子树
2. 让 AIRI 的 Pinia store 与 AuraBot React state 长期双向耦合
3. 直接把 AIRI 的整个 `stage-web` 或 `stage-tamagotchi` 当成 AuraBot renderer 主应用

原因：

1. 运行时不同，维护成本高
2. 状态源会分裂
3. Electron 能力归属会混乱
4. 后续设置、任务、记忆、通知链路会变得很难查错

### 4.3 推荐方案：视觉壳层迁移 + 适配器注入

推荐方案是：

**把 AIRI 的 UI 拆成“页面壳层协议”，AuraBot 提供数据、事件和桌面能力。**

也就是：

1. AIRI 负责“长什么样”
2. AuraBot 负责“数据从哪来、动作怎么执行”

## 5. 推荐接入架构

### 5.1 架构结论

建议在 AuraBot 内新增一层：

- `AiriUiAdapterLayer`

它位于：

1. 上游：AuraBot 现有业务状态与 IPC API
2. 下游：迁移后的 AIRI 风格页面 / 组件壳

### 5.2 分层结构

```text
AuraBot Core / IPC / Stores
        ↓
Aura UI ViewModel Adapters
        ↓
Airi UI Shell Components
        ↓
Electron Windows / Renderer Surfaces
```

### 5.3 各层职责

#### A. AuraBot Core 层

负责真实业务：

1. 会话消息
2. 任务状态
3. 桌宠活动
4. Live2D 模型选择
5. 设置读写
6. 通知投递

#### B. ViewModel Adapter 层

负责把 AuraBot 业务对象转成 AIRI UI 能直接消费的展示模型。

例如：

1. `Message[]` 转成对话时间线项
2. `Task[]` 转成任务卡片项
3. `PublicAppConfig` 转成设置面板分组
4. `PetActivity` 转成舞台提示状态

#### C. Airi UI Shell 层

负责：

1. 布局
2. 动效
3. 场景容器
4. 视觉组件
5. 交互反馈

它不直接依赖 AuraBot 内部实现细节，只消费一组稳定接口。

## 6. 需要定义的接口分层

本次迁移至少要定义五类接口。

### 6.1 场景挂载接口

用于定义 AIRI 风格 UI 要挂到 AuraBot 的哪些窗口和区域。

```ts
export type AuraSurfaceId =
  | "desktop-pet"
  | "console"
  | "settings"
  | "notification-preview";

export interface AuraSurfaceMountSpec {
  surface: AuraSurfaceId;
  variant: string;
  supportsLive2D: boolean;
  supportsChat: boolean;
  supportsTasks: boolean;
  supportsSettings: boolean;
}
```

职责：

1. 规定某个 AIRI 风格场景挂到哪个窗口
2. 规定该场景允许使用哪些 AuraBot 能力
3. 避免一个 UI 壳误接到不对应的业务区域

### 6.2 视图模型接口

用于隔离 AuraBot 原始数据结构和 AIRI UI 消费结构。

```ts
export interface AiriChatTimelineItem {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  createdAt: string;
  attachments: Array<{
    id: string;
    name: string;
    path: string;
    taskId: string;
  }>;
}

export interface AiriTaskCardItem {
  id: string;
  title: string;
  summary: string;
  status: "queued" | "running" | "waiting" | "completed" | "failed" | "interrupted";
  dueAt?: string;
  createdAt: string;
  updatedAt: string;
  canCancel: boolean;
  canDelete: boolean;
}

export interface AiriCompanionState {
  agentStatus: "idle" | "listening" | "thinking" | "typing" | "tasking";
  petNotice?: string;
  petActivityKind?:
    | "task_completed"
    | "voice_reply"
    | "voice_notice"
    | "memory_follow_up"
    | "task_reminder"
    | "chat_reply"
    | "proactive_idle";
}
```

职责：

1. 把内部结构整理成 UI 友好的结构
2. 把 AuraBot 的字段命名和 UI 壳隔离开
3. 给以后换 UI 留出空间

### 6.3 交互动作接口

用于让 AIRI UI 壳触发 AuraBot 行为。

```ts
export interface AiriUiActionBridge {
  sendChatMessage(input: {
    content: string;
    source: "console" | "pet_voice" | "pet_text";
  }): Promise<void>;
  cancelTask(taskId: string): Promise<void>;
  deleteTask(taskId: string): Promise<void>;
  openConsole(): Promise<void>;
  openSettings(): Promise<void>;
  openConfigFile(): Promise<void>;
  setCurrentLive2DModel(modelId: string): Promise<void>;
  updatePetSettings(patch: {
    clickThroughPet?: boolean;
    displayLanguage?: "zh" | "en";
  }): Promise<void>;
}
```

职责：

1. 保证 UI 层不直接碰 IPC channel 名称
2. 把用户动作统一收口为高层行为
3. 避免 AIRI UI 组件知道 AuraBot 内部 preload 或 main process 细节

### 6.4 桌面能力桥接口

用于承接 AIRI 风格 UI 对桌面窗口和系统能力的要求。

```ts
export interface AiriDesktopCapabilityBridge {
  setMousePassthrough(enabled: boolean): Promise<void>;
  movePetWindowBy(delta: { x: number; y: number }): Promise<void>;
  showSystemNotification(input: {
    title: string;
    body: string;
    kind: "follow_up" | "time_reminder" | "idle_nudge";
  }): Promise<void>;
  focusConsoleWindow(): Promise<void>;
}
```

职责：

1. 处理拖拽
2. 处理点击穿透
3. 处理通知回流
4. 处理窗口聚焦

### 6.5 设置页数据接口

用于把 AuraBot 现有设置中心映射成 AIRI 风格设置 UI。

```ts
export interface AiriSettingsSection<TItem = unknown> {
  key: string;
  title: string;
  description?: string;
  items: TItem[];
}

export interface AiriModelProviderItem {
  role: "chatModel" | "gateModel" | "memoryModel";
  provider: string;
  model: string;
  enabled: boolean;
  baseUrl?: string;
  timeoutMs?: number;
  hasApiKey: boolean;
  healthState:
    | "not_configured"
    | "configured"
    | "testing"
    | "connected"
    | "error"
    | "using_test_double";
}

export interface AiriTaskRunnerItem {
  id: "codex" | "claude" | "hermes";
  label: string;
  enabled: boolean;
  priority: number;
  command?: string;
  workingDirectory?: string;
  timeoutMs?: number;
}
```

职责：

1. 让设置 UI 可以按 AIRI 风格重排
2. 但底层数据仍然来自 AuraBot 的 `PublicAppConfig` 和 `PublicModelHealthMap`

## 7. AuraBot 现有能力到迁移接口的映射

### 7.1 聊天

AuraBot 现有来源：

1. `AppInitialState.messages`
2. `onChatMessageCreated`
3. `onChatMessageUpdated`
4. `sendChatMessage`

迁移后映射为：

1. `AiriChatTimelineItem[]`
2. `AiriUiActionBridge.sendChatMessage()`

### 7.2 任务

AuraBot 现有来源：

1. `AppInitialState.tasks`
2. `onTaskChanged`
3. `onTaskDeleted`
4. `cancelTask`
5. `deleteTask`

迁移后映射为：

1. `AiriTaskCardItem[]`
2. `AiriUiActionBridge.cancelTask()`
3. `AiriUiActionBridge.deleteTask()`

### 7.3 桌宠状态

AuraBot 现有来源：

1. `agentStatus`
2. `PetActivity`
3. `PetCursorPosition`
4. `PetSettings`
5. `Live2DModelState`

迁移后映射为：

1. `AiriCompanionState`
2. `AiriDesktopCapabilityBridge`
3. `Live2D` 舞台展示模型

### 7.4 设置中心

AuraBot 现有来源：

1. `PublicAppConfig`
2. `PublicModelHealthMap`
3. `NotificationSettings`
4. `PersonaSettings`
5. `Live2DModelState`
6. `MemoryDebugSnapshot`

迁移后映射为：

1. `AiriSettingsSection[]`
2. 对应更新动作接口

## 8. 各界面的接入要求

### 8.1 桌宠主舞台

目标：

1. 保留 AuraBot 当前 `DesktopPet` 的桌面能力
2. 借鉴 AIRI 的舞台式 UI、轻浮层、角色在场感
3. 不破坏现有点击穿透、拖拽、悬浮聊天、Live2D 挂载链路

接入接口要求：

1. 必须通过 `AiriDesktopCapabilityBridge` 调用桌面动作
2. 必须通过 `AiriCompanionState` 消费状态
3. Live2D 模型切换只能走 AuraBot 的现有模型状态与切换接口

### 8.2 控制台

目标：

1. 保留 AuraBot 现有主会话能力
2. 借用 AIRI 的聊天场景布局、卡片感、工具条和动效语言
3. 不引入 AIRI 原生路由或 Pinia store

接入接口要求：

1. 消息区只消费 `AiriChatTimelineItem[]`
2. 任务区只消费 `AiriTaskCardItem[]`
3. 输入框动作统一走 `sendChatMessage`

### 8.3 设置页

目标：

1. 保留 AuraBot 现有设置项和更新逻辑
2. 借用 AIRI 设置页的排版、组块、视觉语义
3. 不让设置 UI 直接调用配置文件或 main process

接入接口要求：

1. 只消费格式化后的 section 数据
2. 所有更新动作走桥接接口
3. `Memory Debug` 继续作为深层页存在，不回到控制台正文

### 8.4 通知与轻提示

目标：

1. 让 AIRI 风格的 toast / stage bubble / notice bar 承接 AuraBot 当前提醒链路
2. 保持系统通知仍由 AuraBot 主链路负责

接入接口要求：

1. UI 层只能声明“展示什么”
2. 真实系统通知仍由 AuraBot 通知能力桥投递

## 9. 不同迁移路径的取舍

### 9.1 路径 A：视觉复刻

做法：

1. 参考 AIRI 的布局、颜色、动效
2. 用 AuraBot React 组件重写

优点：

1. 技术栈统一
2. 可维护性最高
3. 与现有 IPC / 状态体系最稳

缺点：

1. 初期工作量较大
2. 不能直接复用 AIRI 现成 Vue 组件

### 9.2 路径 B：壳层协议迁移

做法：

1. 抽象 AIRI UI 的页面协议和组件协议
2. 在 AuraBot 内重建一层 AIRI 风格壳层

优点：

1. 更接近原 UI 结构
2. 能保留较多页面组织方式
3. 适合分阶段替换 AuraBot 现有界面

缺点：

1. 需要先做适配器层
2. 设计和工程都要做一轮翻译

### 9.3 路径 C：直接嵌入 AIRI 子应用

做法：

1. 把 AIRI 的某个 app 当成子前端嵌入 AuraBot

优点：

1. 最接近原始 AIRI 界面

缺点：

1. React / Vue 双运行时长期并存
2. 状态桥复杂
3. 桌面窗口和 Electron 能力归属混乱
4. 后期维护风险很大

### 9.4 推荐

推荐采用：

**路径 B 为主，路径 A 为辅。**

解释：

1. 先按壳层协议定义清楚 AIRI UI 想消费什么
2. 再在 AuraBot 中用 React 实现这些壳层
3. 对特别有价值的视觉细节，再从 AIRI 逐步复刻

这比“直接嵌 AIRI”稳定得多，也比“纯凭感觉重画”更接近原项目。

## 10. 第一版必须落地的接口清单

第一版建议只先定义并打通以下接口，不要一上来把 AIRI 全量页面都搬进来。

### 10.1 必做

1. `AiriChatTimelineItem`
2. `AiriTaskCardItem`
3. `AiriCompanionState`
4. `AiriUiActionBridge`
5. `AiriDesktopCapabilityBridge`
6. `AiriSettingsSection`

### 10.2 可第二阶段再补

1. 多场景主题 token 接口
2. 动效预设注册接口
3. Live2D 多模型材质扩展接口
4. 调试页专属展示接口

## 11. 实施顺序建议

### 阶段 1：先做接口，不动大 UI

目标：

1. 在 AuraBot 中补齐 `AiriUiAdapterLayer`
2. 把现有数据全部转成新 ViewModel
3. 不急着替换所有页面视觉

### 阶段 2：先替换控制台

原因：

1. 控制台的业务边界最清晰
2. 聊天与任务卡最容易验证
3. 出问题也不影响桌宠主舞台

### 阶段 3：替换桌宠主舞台

原因：

1. 桌宠牵涉点击穿透、拖拽、悬浮输入、Live2D
2. 风险高于普通页面

### 阶段 4：替换设置中心

原因：

1. 设置项多但交互相对稳定
2. 更适合在前面接口验证完成后收口

## 12. 验收标准

完成第一版接口迁移后，应满足：

1. AIRI 风格 UI 不直接依赖 AuraBot 内部 IPC channel 常量
2. UI 层不直接读写配置文件
3. UI 层不直接持有 AuraBot 主业务 store
4. 聊天、任务、桌宠、设置至少有一层稳定 ViewModel 适配
5. React 渲染主树仍然是 AuraBot 唯一主树
6. Electron 窗口控制仍然由 AuraBot 主进程负责

## 13. 明确不做的事

当前这份接口文档明确不支持以下方向作为默认方案：

1. 把 AIRI 的整套 Vue router 直接接管 AuraBot renderer
2. 在 AuraBot 内长期维护 React + Vue 双主前端
3. 让 AIRI 的 Pinia store 成为 AuraBot 的真实业务状态源
4. 为了“看起来搬得快”而跳过适配器层

## 14. 最终结论

对于 AuraBot 而言，AIRI UI 的接入方式应定义为：

**独立前端工程 UI 迁移。**

它的正确打开方式不是：

1. 复制页面
2. 生硬嵌入
3. 双运行时混搭

而是：

1. 先抽 UI 壳层协议
2. 再做 ViewModel 适配
3. 再把 AIRI 风格界面逐块挂到 AuraBot 的桌宠、控制台、设置中心上

如果后续要继续开工，下一份文档应该直接写：

**`AiriUiAdapterLayer` 的实现计划与接口落库清单。**
