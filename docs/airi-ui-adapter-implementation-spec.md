# AIRI UI 迁移实施接口细化文档

## 1. 文档定位

这份文档是对 [airi-ui-migration-interface-spec.md](/Users/xuan/Documents/AuraBot/docs/airi-ui-migration-interface-spec.md) 的继续细化。

上一份文档回答的是：

1. AIRI 属于什么类型的 UI 工程
2. 为什么不能直接整仓硬嵌
3. 应该抽什么层

这一份文档回答的是：

1. 具体应该改 AuraBot 哪些文件
2. 应该新增哪些文件
3. 每个文件暴露哪些接口和方法
4. AIRI 风格的视觉壳、布局和交互，如何准确套到 AuraBot 现有能力上

## 2. AuraBot 当前真实调用链

在开始搬 UI 之前，必须先明确 AuraBot 现有能力的真实流向。

当前主链路是：

```text
App.tsx
  -> ConsoleWindow / DesktopPet / SettingsWindow
  -> useAuraBotStore (zustand)
  -> window.aurabot (preload 暴露)
  -> IPC handlers
  -> main / core services
```

对应到真实文件：

1. 渲染入口：[src/renderer/src/App.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/App.tsx)
2. 前端状态与动作入口：[src/renderer/src/app/store.ts](/Users/xuan/Documents/AuraBot/src/renderer/src/app/store.ts)
3. 渲染层窗口组件：
   - [src/renderer/src/features/console/ConsoleWindow.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/console/ConsoleWindow.tsx)
   - [src/renderer/src/features/pet/DesktopPet.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/pet/DesktopPet.tsx)
   - [src/renderer/src/features/settings/SettingsWindow.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/SettingsWindow.tsx)
4. preload 桥：[src/preload/index.ts](/Users/xuan/Documents/AuraBot/src/preload/index.ts)
5. IPC 契约：[src/shared/contracts/ipc.ts](/Users/xuan/Documents/AuraBot/src/shared/contracts/ipc.ts)
6. IPC 注册：[src/main/registerIpc.ts](/Users/xuan/Documents/AuraBot/src/main/registerIpc.ts)
7. 窗口与广播实现：[src/main/createWindow.ts](/Users/xuan/Documents/AuraBot/src/main/createWindow.ts)

这条链路决定了 AIRI UI 应该接在哪里。

结论是：

**AIRI UI 不应该直接接到 `window.aurabot`，而应该先接到 renderer 层的适配器和 bridge，再由 bridge 去调用现有 store / preload / IPC。**

## 3. 迁移时不要动的层

下面这些层在 UI 迁移第一阶段不要重构：

1. `src/core/**`
2. `src/main/registerIpc.ts` 里现有业务语义
3. `src/preload/index.ts` 里现有 API 语义
4. `src/renderer/src/app/store.ts` 的业务动作命名

原因不是这些层永远不能改，而是 AIRI UI 迁移的目标是先换“外观与交互壳”，不是先重写底层业务管线。

## 4. 推荐的新文件布局

建议在 renderer 内新增一个独立模块：

```text
src/renderer/src/features/airi/
  contracts.ts
  adapters/
    timelineAdapter.ts
    taskPanelAdapter.ts
    companionAdapter.ts
    settingsAdapter.ts
    consoleViewModel.ts
    petViewModel.ts
    settingsViewModel.ts
  bridges/
    useAiriActionBridge.ts
    useAiriDesktopBridge.ts
  components/
    AiriConsoleShell.tsx
    AiriChatTimeline.tsx
    AiriTaskPanel.tsx
    AiriPetShell.tsx
    AiriPetToolbar.tsx
    AiriHoverChatCard.tsx
    AiriSettingsShell.tsx
    AiriSettingsSidebar.tsx
    AiriSettingsSection.tsx
```

这个目录只放三类东西：

1. AIRI 风格 UI 要吃的类型
2. 从 AuraBot 数据到 AIRI 视图模型的适配函数
3. 真正的 AIRI 风格视觉组件

## 5. 每个新增文件应该负责什么

### 5.1 `contracts.ts`

文件：

- 计划新增：`src/renderer/src/features/airi/contracts.ts`

职责：

1. 定义 AIRI 壳层只关心的 props 结构
2. 切断 AIRI 壳和 AuraBot 原始 schema 的直接耦合

建议导出的类型：

```ts
export interface AiriTimelineAttachment {
  id: string;
  name: string;
  path: string;
  taskId: string;
}

export interface AiriTimelineItem {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  authorLabel: string;
  segments: string[];
  attachments: AiriTimelineAttachment[];
  createdAt: string;
}

export interface AiriTaskPanelCard {
  id: string;
  title: string;
  subtitle: string;
  status: "queued" | "running" | "waiting" | "completed" | "failed" | "interrupted";
  canCancel: boolean;
  canDelete: boolean;
}

export interface AiriConsoleViewModel {
  title: string;
  subtitle: string;
  agentStatusLabel: string;
  timeline: AiriTimelineItem[];
  taskCards: AiriTaskPanelCard[];
}

export interface AiriPetViewModel {
  stageTitle: string;
  stageSubtitle?: string;
  agentStatus: "idle" | "listening" | "thinking" | "typing" | "tasking";
  activityMessage?: string;
  activityKind?:
    | "task_completed"
    | "voice_reply"
    | "voice_notice"
    | "memory_follow_up"
    | "task_reminder"
    | "chat_reply"
    | "proactive_idle";
  taskCards: AiriTaskPanelCard[];
  currentModelPath?: string;
}

export interface AiriSettingsNavItem {
  key: string;
  label: string;
  description?: string;
}

export interface AiriSettingsViewModel {
  title: string;
  subtitle: string;
  navItems: AiriSettingsNavItem[];
}
```

### 5.2 `adapters/timelineAdapter.ts`

职责：

1. 把 AuraBot 的 `Message[]` 转成 AIRI 时间线项
2. 把助手消息按句切段，适配 AIRI 风格气泡流

建议方法：

```ts
import type { Message } from "../../../../../shared/schemas/message";
import type { PetSettings } from "../../../../../shared/schemas/petSettings";
import type { AiriTimelineItem } from "../contracts";

export function mapMessagesToAiriTimeline(
  messages: Message[],
  displayLanguage: PetSettings["displayLanguage"]
): AiriTimelineItem[];

export function mapMessageToAiriTimelineItem(
  message: Message,
  displayLanguage: PetSettings["displayLanguage"]
): AiriTimelineItem;
```

实现要点：

1. 复用 `splitTextIntoSentences()` 的分段能力
2. 把 `message.attachments` 原样映射成 AIRI attachment 数据
3. 在这里生成 `authorLabel`，而不是在 AIRI 组件里判断 `role`

已有可复用逻辑来源：

- [src/renderer/src/features/chat/MessageList.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/chat/MessageList.tsx)
- [src/shared/text/sentences.ts](/Users/xuan/Documents/AuraBot/src/shared/text/sentences.ts)

### 5.3 `adapters/taskPanelAdapter.ts`

职责：

1. 把 `Task[]` 转成 AIRI 任务面板卡片
2. 统一任务标题、状态副文案和按钮可用性

建议方法：

```ts
import type { Task } from "../../../../../shared/schemas/task";
import type { PetSettings } from "../../../../../shared/schemas/petSettings";
import type { AiriTaskPanelCard } from "../contracts";

export function mapTasksToAiriTaskCards(
  tasks: Task[],
  displayLanguage: PetSettings["displayLanguage"],
  options?: {
    limit?: number;
    includeSettled?: boolean;
  }
): AiriTaskPanelCard[];

export function mapTaskToAiriTaskCard(
  task: Task,
  displayLanguage: PetSettings["displayLanguage"]
): AiriTaskPanelCard;
```

实现要点：

1. 复用 `pickConsoleStickyTasks()` 的排序策略
2. 复用 `isStickyTaskActive()` 和 `isStickyTaskSettled()` 来确定按钮状态
3. 在 adapter 里决定：
   - `canCancel`
   - `canDelete`
   - `subtitle`

已有可复用逻辑来源：

- [src/renderer/src/features/tasks/TaskStickyNotes.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/tasks/TaskStickyNotes.tsx)

### 5.4 `adapters/companionAdapter.ts`

职责：

1. 把桌宠当前状态整理成 AIRI 舞台层能直接消费的状态对象
2. 把 `PetActivity`、`agentStatus`、`tasks`、`modelPath` 组合成桌宠舞台 view model

建议方法：

```ts
import type { AgentStatus } from "../../../../../shared/contracts/chat";
import type { PetActivity } from "../../../../../shared/contracts/pet";
import type { Task } from "../../../../../shared/schemas/task";
import type { AiriPetViewModel } from "../contracts";
import type { PetSettings } from "../../../../../shared/schemas/petSettings";

export function buildAiriPetViewModel(input: {
  agentStatus: AgentStatus;
  petActivity?: PetActivity;
  tasks: Task[];
  modelPath?: string;
  petSettings: PetSettings;
}): AiriPetViewModel;
```

实现要点：

1. `activityMessage` 直接来自 `petActivity.message`
2. `taskCards` 只取桌宠需要展示的少量任务
3. `agentStatus` 保持原始枚举，不在 UI 组件里猜状态

### 5.5 `adapters/settingsAdapter.ts`

职责：

1. 把 AuraBot 现有设置中心的数据整理成 AIRI 风格设置壳层数据
2. 把原来散在页面里的标题、描述、卡片元信息集中整理

建议方法：

```ts
import type { PublicAppConfig, PublicModelHealthMap } from "../../../../../shared/contracts/settings";
import type { NotificationSettings } from "../../../../../shared/contracts/notificationSettings";
import type { PersonaSettings } from "../../../../../shared/contracts/personaSettings";
import type { Live2DModelState } from "../../../../../shared/contracts/live2dModel";
import type { MemoryDebugSnapshot } from "../../../../../shared/contracts/ipc";
import type { PetSettings } from "../../../../../shared/schemas/petSettings";
import type { AiriSettingsNavItem, AiriSettingsViewModel } from "../contracts";

export function buildAiriSettingsViewModel(input: {
  displayLanguage: PetSettings["displayLanguage"];
  currentPage: string;
}): AiriSettingsViewModel;

export function buildAiriSettingsNavItems(
  displayLanguage: PetSettings["displayLanguage"]
): AiriSettingsNavItem[];
```

注意：

这里先只负责壳层导航与页面级元信息，不把每个设置页的表单字段都再抽象一遍。因为 AuraBot 当前各页：

1. 模型页
2. 任务 CLI 页
3. 通知页
4. 人格页
5. Live2D 模型页
6. 记忆调试页

本身已经能跑，第一阶段只需要用 AIRI 的壳层把它们包起来。

### 5.6 `adapters/consoleViewModel.ts`

职责：

1. 聚合聊天、任务、状态文案
2. 生成给 `AiriConsoleShell` 的单一 view model

建议方法：

```ts
import type { AgentStatus } from "../../../../../shared/contracts/chat";
import type { Message } from "../../../../../shared/schemas/message";
import type { Task } from "../../../../../shared/schemas/task";
import type { PetSettings } from "../../../../../shared/schemas/petSettings";
import type { AiriConsoleViewModel } from "../contracts";

export function buildAiriConsoleViewModel(input: {
  agentStatus: AgentStatus;
  messages: Message[];
  tasks: Task[];
  petSettings: PetSettings;
}): AiriConsoleViewModel;
```

实现要点：

1. 内部调用 `mapMessagesToAiriTimeline()`
2. 内部调用 `mapTasksToAiriTaskCards()`
3. 顶部标题和副标题继续走 AuraBot 的 i18n

### 5.7 `bridges/useAiriActionBridge.ts`

职责：

1. 把 UI 可触发的动作统一收口成一个 bridge
2. 不让 AIRI 组件直接 import `useAuraBotStore`

建议方法：

```ts
import type {
  ModelRoleKey,
  ModelProviderOption,
  TaskRunnerSettingsPatch,
  ProactiveSettingsPatch
} from "../../../../../shared/contracts/settings";
import type { PersonaSettings } from "../../../../../shared/contracts/personaSettings";
import type { NotificationSettings } from "../../../../../shared/contracts/notificationSettings";
import type { PetSettingsPatch } from "../../../../../shared/schemas/petSettings";

export interface AiriActionBridge {
  sendConsoleMessage(content: string): Promise<void>;
  sendPetTextMessage(content: string): Promise<void>;
  sendPetVoiceMessage(content: string): Promise<void>;
  cancelTask(taskId: string): Promise<void>;
  deleteTask(taskId: string): Promise<void>;
  openConsole(): Promise<void>;
  openSettings(): Promise<void>;
  openConfigFile(): Promise<void>;
  updatePetSettings(patch: PetSettingsPatch): Promise<void>;
  updateTaskRunnerSettings(runners: TaskRunnerSettingsPatch["runners"]): Promise<void>;
  updateModelProvider(role: ModelRoleKey, provider: ModelProviderOption): Promise<void>;
  testModelConnection(role: ModelRoleKey): Promise<void>;
  updateProactiveSettings(patch: ProactiveSettingsPatch): Promise<void>;
  updatePersonaSettings(settings: PersonaSettings): Promise<void>;
  setCurrentLive2DModel(modelId: string): Promise<void>;
  updateNotificationSettings(
    patch: Partial<Omit<NotificationSettings, "permissionState">>
  ): Promise<void>;
  refreshMemoryDebugSnapshot(): Promise<void>;
}

export function useAiriActionBridge(): AiriActionBridge;
```

这个 hook 内部不要直接碰 `window.aurabot`，而是只调用现有 store 方法：

1. `sendMessage`
2. `cancelTask`
3. `deleteTask`
4. `openConsoleWindow`
5. `openSettingsWindow`
6. `openConfigFile`
7. `updatePetSettings`
8. `updateTaskRunnerSettings`
9. `updateModelProviderSettings`
10. `testModelConnection`
11. `updateProactiveSettings`
12. `updatePersonaSettings`
13. `setCurrentLive2DModel`
14. `updateNotificationSettings`
15. `loadMemoryDebugSnapshot`

这些方法都已经存在于：

- [src/renderer/src/app/store.ts](/Users/xuan/Documents/AuraBot/src/renderer/src/app/store.ts)

### 5.8 `bridges/useAiriDesktopBridge.ts`

职责：

1. 包装桌宠舞台相关的桌面交互能力
2. 把拖拽、点击穿透、轻提示之类的桌面能力从 UI 结构里解耦

建议方法：

```ts
export interface AiriDesktopBridge {
  setMousePassthrough(enabled: boolean): Promise<void>;
  movePetWindowBy(delta: { x: number; y: number }): Promise<void>;
  showPetNotice(message: string): void;
  clearPetActivity(id?: string): void;
}

export function useAiriDesktopBridge(): AiriDesktopBridge;
```

内部复用 store 方法：

1. `setPetMousePassthrough`
2. `movePetWindowBy`
3. `showPetNotice`
4. `clearPetActivity`

### 5.9 `components/AiriConsoleShell.tsx`

职责：

1. 只负责 AIRI 风格控制台的外观和布局
2. 不直接碰 `Task`、`Message`、`PetSettings`

建议 props：

```ts
import type { AiriConsoleViewModel } from "../contracts";
import type { AiriActionBridge } from "../bridges/useAiriActionBridge";

export interface AiriConsoleShellProps {
  viewModel: AiriConsoleViewModel;
  actions: Pick<AiriActionBridge, "sendConsoleMessage" | "cancelTask" | "deleteTask" | "openSettings">;
}

export function AiriConsoleShell(props: AiriConsoleShellProps): JSX.Element;
```

### 5.10 `components/AiriPetShell.tsx`

职责：

1. 负责桌宠周围 AIRI 风格的视觉壳
2. 不接管现有语音、拖拽、悬浮输入逻辑

建议 props：

```ts
import type { AiriPetViewModel } from "../contracts";
import type { AiriActionBridge } from "../bridges/useAiriActionBridge";
import type { AiriDesktopBridge } from "../bridges/useAiriDesktopBridge";
import type { ReactNode } from "react";

export interface AiriPetShellProps {
  viewModel: AiriPetViewModel;
  desktop: AiriDesktopBridge;
  actions: Pick<AiriActionBridge, "openConsole" | "openSettings" | "sendPetTextMessage" | "sendPetVoiceMessage">;
  live2dCanvas: ReactNode;
  hoverToolbar: ReactNode;
  hoverChatCard: ReactNode;
}

export function AiriPetShell(props: AiriPetShellProps): JSX.Element;
```

重点：

`DesktopPet.tsx` 里复杂的交互方法不要丢，只是把“长什么样”迁出去。

### 5.11 `components/AiriSettingsShell.tsx`

职责：

1. 负责 AIRI 风格设置中心壳层
2. 保留 AuraBot 当前页面切换逻辑

建议 props：

```ts
import type { ReactNode } from "react";
import type { AiriSettingsViewModel } from "../contracts";

export interface AiriSettingsShellProps {
  viewModel: AiriSettingsViewModel;
  currentPage: string;
  onSelect(page: string): void;
  children: ReactNode;
}

export function AiriSettingsShell(props: AiriSettingsShellProps): JSX.Element;
```

## 6. 应该改的现有文件，以及怎么改

### 6.1 `App.tsx`

文件：

- [src/renderer/src/App.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/App.tsx)

建议改法：

不要在 `App.tsx` 直接塞大量 AIRI JSX。

`App.tsx` 应继续只做：

1. surface 选择
2. store 数据提取
3. 把 props 传给 surface 组件

如果要支持渐进替换，可以新增一个简单开关：

```ts
type UiFlavor = "legacy" | "airi";
const uiFlavor: UiFlavor = "airi";
```

然后把它传入：

1. `ConsoleWindow`
2. `DesktopPet`
3. `SettingsWindow`

示例方法签名变化：

```ts
interface ConsoleWindowProps {
  uiFlavor?: "legacy" | "airi";
  ...
}
```

### 6.2 `ConsoleWindow.tsx`

文件：

- [src/renderer/src/features/console/ConsoleWindow.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/console/ConsoleWindow.tsx)

建议定位：

它继续作为“控制台 surface controller”，不要再让它承担最终视觉细节。

具体改法：

1. 继续接收现在这些业务 props
2. 在文件内调用 `buildAiriConsoleViewModel()`
3. 在文件内调用 `useAiriActionBridge()` 或从上层传 actions
4. 最终渲染 `AiriConsoleShell`

建议新增方法：

```ts
function renderLegacyConsole(props: ConsoleWindowProps): JSX.Element;
function renderAiriConsole(props: ConsoleWindowProps): JSX.Element;
```

或者更简单：

```ts
export function ConsoleWindow(props: ConsoleWindowProps) {
  if (props.uiFlavor === "airi") {
    const viewModel = buildAiriConsoleViewModel(...);
    return <AiriConsoleShell viewModel={viewModel} actions={...} />;
  }

  return <LegacyConsoleWindowLayout ... />;
}
```

### 6.3 `DesktopPet.tsx`

文件：

- [src/renderer/src/features/pet/DesktopPet.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/pet/DesktopPet.tsx)

建议定位：

它继续作为“桌宠交互控制器”，不要把复杂 hover / drag / voice 逻辑拆散到 AIRI 壳里。

必须保留在 `DesktopPet.tsx` 的现有方法：

1. `setHoverPassthrough()`
2. `handlePetPointerMove()`
3. `handleVoiceClick()`
4. `handleControlsPointerEnter()`
5. `handleControlsPointerLeave()`
6. `handleModelPointerEnter()`
7. `handleModelPointerLeave()`
8. `updateHoverChatSide()`
9. `sendHoverChatContent()`

这些都是 AuraBot 当前已验证过的桌面交互行为，不能为了“换 AIRI UI”把它们打碎。

正确做法：

1. `DesktopPet.tsx` 继续管理这些状态和事件
2. 把 `Live2DModelCanvas`、悬浮按钮、输入框卡片、活动气泡这些视觉区域交给 `AiriPetShell`

建议新增方法：

```ts
function buildPetShellNodes(): {
  live2dCanvas: ReactNode;
  hoverToolbar: ReactNode;
  hoverChatCard: ReactNode;
};
```

### 6.4 `SettingsWindow.tsx`

文件：

- [src/renderer/src/features/settings/SettingsWindow.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/SettingsWindow.tsx)

建议定位：

它继续作为“设置中心路由器”。

要保留的现有能力：

1. `currentPage` 状态
2. `navItems`
3. 现有页面组件
4. 每个设置页的更新动作

正确改法：

1. 用 `buildAiriSettingsViewModel()` 生成壳层标题与导航元信息
2. 用 `AiriSettingsShell` 替换 `SettingsCenterShell`
3. 现有页面组件先不大拆

也就是说第一阶段不重写：

1. `ModelSettingsPage.tsx`
2. `TaskCliSettingsPage.tsx`
3. `NotificationSettingsPage.tsx`
4. `PersonaSettingsPage.tsx`
5. `Live2DModelSettingsPage.tsx`
6. `MemoryDebugSettingsPage.tsx`

而是先让 AIRI 风格壳把它们包进去。

### 6.5 `styles.css`

文件：

- [src/renderer/src/styles.css](/Users/xuan/Documents/AuraBot/src/renderer/src/styles.css)

建议定位：

第一阶段只做两件事：

1. 新增 AIRI UI 命名空间样式
2. 不要粗暴覆盖现有所有类名

推荐写法：

```css
.airi-console-shell { ... }
.airi-chat-timeline { ... }
.airi-task-panel { ... }
.airi-pet-shell { ... }
.airi-settings-shell { ... }
```

不要第一轮就去把：

1. `.console-window`
2. `.message-list`
3. `.settings-window`

整组覆写成 AIRI 风格，否则很难逐步回退和对比。

## 7. 现有 AuraBot 能力和 AIRI UI 的具体对接方式

### 7.1 聊天时间线

现有 AuraBot 能力来源：

1. `useAuraBotStore((state) => state.messages)`
2. `useAuraBotStore((state) => state.sendMessage)`

接法：

1. `ConsoleWindow.tsx` 取出 `messages`
2. `timelineAdapter.ts` 转成 `AiriTimelineItem[]`
3. `AiriChatTimeline.tsx` 只负责渲染
4. 输入框提交调用 `actions.sendConsoleMessage()`

明确不要：

1. 在 `AiriChatTimeline.tsx` 里 import `window.aurabot`
2. 在 `AiriChatTimeline.tsx` 里操作 store

### 7.2 任务面板

现有 AuraBot 能力来源：

1. `useAuraBotStore((state) => state.tasks)`
2. `cancelTask(taskId)`
3. `deleteTask(taskId)`

接法：

1. `taskPanelAdapter.ts` 生成 `AiriTaskPanelCard[]`
2. `AiriTaskPanel.tsx` 渲染横向滚动、按钮右置的任务卡
3. 点击按钮时调用 `actions.cancelTask()` / `actions.deleteTask()`

### 7.3 桌宠悬浮交互

现有 AuraBot 能力来源：

1. `DesktopPet.tsx` 的本地状态
2. `setPetMousePassthrough()`
3. `movePetWindowBy()`
4. `showPetNotice()`
5. `clearPetActivity()`

接法：

1. AIRI 壳只接收 `viewModel`
2. 桌面动作全部经 `useAiriDesktopBridge()`
3. 语音和文本发送动作全部经 `useAiriActionBridge()`

### 7.4 设置页动作

现有 AuraBot 能力来源：

1. `updateTaskRunnerSettings()`
2. `updateModelProviderSettings()`
3. `testModelConnection()`
4. `updateProactiveSettings()`
5. `updatePersonaSettings()`
6. `setCurrentLive2DModel()`
7. `updateNotificationSettings()`
8. `loadMemoryDebugSnapshot()`

接法：

1. 设置页壳层只改布局
2. 原始业务动作继续由现有页面组件触发
3. 后续如果 AIRI 要求完全不同的设置控件，再逐页做二次抽象

## 8. 如果 AIRI 壳需要 AuraBot 当前没有的新能力，应该改哪几个文件

这是最关键的落地点。

比如 AIRI UI 后面如果需要：

1. 获取控制台窗口边界
2. 获取窗口尺寸信息
3. 预览通知样式
4. 设置桌宠窗口额外状态

则必须按下面顺序加能力。

### 第 1 步：改 IPC 契约

文件：

- [src/shared/contracts/ipc.ts](/Users/xuan/Documents/AuraBot/src/shared/contracts/ipc.ts)

新增：

1. `IPC_CHANNELS.xxx`
2. `AuraBotApi.xxx()`
3. 入参 / 返回值类型

示例：

```ts
getConsoleWindowBounds: "window:get-console-bounds"
```

### 第 2 步：改 preload 暴露

文件：

- [src/preload/index.ts](/Users/xuan/Documents/AuraBot/src/preload/index.ts)

新增：

```ts
getConsoleWindowBounds: () => ipcRenderer.invoke(IPC_CHANNELS.getConsoleWindowBounds)
```

### 第 3 步：改 main IPC 注册

文件：

- [src/main/registerIpc.ts](/Users/xuan/Documents/AuraBot/src/main/registerIpc.ts)

这里除了新增 handler，还要同步扩展这个文件顶部定义的 `WindowController` interface。

新增 handler：

```ts
ipcMain.handle(IPC_CHANNELS.getConsoleWindowBounds, () => {
  return windows.getConsoleWindowBounds();
});
```

### 第 4 步：改窗口实现

文件：

- [src/main/createWindow.ts](/Users/xuan/Documents/AuraBot/src/main/createWindow.ts)

新增实际方法：

```ts
export function getConsoleWindowBounds(): WindowRectangle | undefined {
  if (!consoleWindow || consoleWindow.isDestroyed()) {
    return undefined;
  }

  return consoleWindow.getBounds();
}
```

### 第 5 步：改 store 动作

文件：

- [src/renderer/src/app/store.ts](/Users/xuan/Documents/AuraBot/src/renderer/src/app/store.ts)

新增：

```ts
async getConsoleWindowBounds() {
  return await window.aurabot.getConsoleWindowBounds();
}
```

### 第 6 步：接到 AIRI bridge

文件：

- `src/renderer/src/features/airi/bridges/useAiriActionBridge.ts`

新增：

```ts
getConsoleWindowBounds(): Promise<{ x: number; y: number; width: number; height: number } | undefined>;
```

**以后 AIRI 壳要新桌面能力，一律按这 6 步走。**

## 9. 第一阶段建议的真实改动顺序

### 步骤 1

先新增这些文件，不改现有主布局：

1. `contracts.ts`
2. `timelineAdapter.ts`
3. `taskPanelAdapter.ts`
4. `companionAdapter.ts`
5. `settingsAdapter.ts`
6. `consoleViewModel.ts`
7. `petViewModel.ts`
8. `settingsViewModel.ts`
9. `useAiriActionBridge.ts`
10. `useAiriDesktopBridge.ts`

### 步骤 2

做 `AiriConsoleShell.tsx`，并在 `ConsoleWindow.tsx` 内接起来。

原因：

1. 风险最低
2. 业务边界最清晰
3. 对比效果最容易看

### 步骤 3

做 `AiriPetShell.tsx`，但保留 `DesktopPet.tsx` 现有交互控制逻辑。

### 步骤 4

做 `AiriSettingsShell.tsx`，把设置中心换壳，不急着重写所有设置页内容组件。

## 10. 一句话落地结论

把 AIRI 的视觉壳、布局和交互套到 AuraBot 现有能力上，最稳的方式不是：

1. 让 AIRI Vue 组件直接操作 `window.aurabot`
2. 让 AIRI UI 直接依赖 AuraBot 原始业务类型
3. 让每个新组件自己去拿 store

而是：

1. 在 renderer 层新增 `features/airi`
2. 用 adapter 把 `Message / Task / PetActivity / Config` 变成 AIRI view model
3. 用 bridge 把 `sendMessage / cancelTask / movePetWindowBy / updateModelProviderSettings` 这些现有动作包起来
4. 在 `ConsoleWindow / DesktopPet / SettingsWindow` 这三个现有 surface controller 上换 AIRI 壳

这样做，才能既把 AIRI 的“长相”搬过来，又不把 AuraBot 现有能力链路拆坏。
