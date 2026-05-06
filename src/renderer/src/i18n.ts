import type { AgentStatus } from "../../shared/contracts/chat";
import type { PetSettings } from "../../shared/schemas/petSettings";
import type { Task } from "../../shared/schemas/task";

export type DisplayLanguage = PetSettings["displayLanguage"];

const UI_TEXT = {
  en: {
    appTitle: "AuraBot",
    consoleTitle: "AuraBot Console",
    consoleSubtitle: "Chat here when the desktop companion needs a full thread.",
    mainConversation: "Main conversation",
    conversationMessages: "Conversation messages",
    readyWhenYouAre: "Ready when you are.",
    saySomething: "Say something",
    send: "Send",
    you: "You",
    aura: "Aura",
    openFile: "Open file",
    showInFolder: "Show in folder",
    settings: "Settings",
    openSettingsMenu: "Open settings menu",
    settingsMenu: "Settings menu",
    quickSettings: "Quick settings",
    openFullSettings: "Open full settings",
    productSettingsSubtitle: "Product-level configuration for AuraBot.",
    general: "General",
    language: "Language",
    english: "English",
    chinese: "中文",
    model: "Model",
    modelSettings: "Model settings",
    configNotLoaded: "Config not loaded",
    openConfigFile: "Open config file",
    openConfigFileHint: "Edit API keys, base URLs, and model names in config.json.",
    character: "Character",
    characterSettings: "Character settings",
    clickThroughCharacter: "Click through character",
    provider: "Provider",
    apiKey: "API key",
    apiKeySaved: "API key saved",
    notSet: "Not set",
    enabled: "Enabled",
    disabled: "Disabled",
    chatModel: "Chat",
    gateModel: "Gate",
    memoryModel: "Memory",
    taskCli: "Task CLI",
    taskCliSettings: "Task CLI settings",
    taskCliHint: "Choose which command-line agent AuraBot should use for tasks. Lower priority runs first.",
    taskRunner: "task runner",
    taskRunnerPriority: "task runner priority",
    enable: "Enable",
    priority: "Priority",
    petQuickControls: "Pet quick controls",
    dragPet: "Drag pet",
    drag: "Drag",
    startVoiceInput: "Start voice input",
    stopVoiceRecording: "Stop voice recording",
    voice: "Voice",
    openAuraBotConsole: "Open AuraBot console",
    console: "Console",
    openAuraBotSettings: "Open AuraBot settings",
    petQuickChat: "Pet quick chat",
    petChatPlaceholder: "Send a message to the main chat",
    sendPetChat: "Send pet chat",
    openPetQuickChat: "Open pet quick chat",
    chat: "Chat",
    petMessage: "Pet message",
    desktopLive2DPet: "Desktop Live2D pet",
    interactWithAuraBot: "Interact with AuraBot",
    memoryDebugTools: "Memory debug tools",
    memoryDebug: "Memory Debug",
    refresh: "Refresh",
    memoryDebugPanel: "Memory debug panel",
    memoryDebugMetrics: "Memory debug metrics",
    nodes: "Nodes",
    edges: "Edges",
    summaries: "Summaries",
    events: "Events",
    recentEvents: "Recent Events",
    noMemorySnapshot: "No memory snapshot loaded.",
    lastMemoryRecallTrace: "Last memory recall trace",
    lastRecall: "Last Recall",
    seeds: "Seeds",
    expanded: "Expanded",
    returned: "Returned",
    taskStatus: "Task status",
    cancel: "Cancel",
    delete: "Delete",
    cancelTask: "Cancel task",
    deleteTask: "Delete task"
  },
  zh: {
    appTitle: "AuraBot",
    consoleTitle: "AuraBot 控制台",
    consoleSubtitle: "桌宠需要完整对话线程时，在这里继续聊。",
    mainConversation: "主对话",
    conversationMessages: "对话消息",
    readyWhenYouAre: "我在，随时可以开始。",
    saySomething: "说点什么",
    send: "发送",
    you: "你",
    aura: "Aura",
    openFile: "打开文件",
    showInFolder: "所在文件夹",
    settings: "设置",
    openSettingsMenu: "打开设置菜单",
    settingsMenu: "设置菜单",
    quickSettings: "快捷设置",
    openFullSettings: "打开完整设置",
    productSettingsSubtitle: "AuraBot 的产品级配置。",
    general: "通用",
    language: "语言",
    english: "English",
    chinese: "中文",
    model: "模型",
    modelSettings: "模型设置",
    configNotLoaded: "配置未加载",
    openConfigFile: "打开 config 文件",
    openConfigFileHint: "在 config.json 里编辑 API Key、Base URL 和模型名。",
    character: "人物",
    characterSettings: "人物设置",
    clickThroughCharacter: "穿透人物点击",
    provider: "服务商",
    apiKey: "API Key",
    apiKeySaved: "API Key 已保存",
    notSet: "未设置",
    enabled: "已启用",
    disabled: "未启用",
    chatModel: "聊天",
    gateModel: "判断",
    memoryModel: "记忆",
    taskCli: "任务 CLI",
    taskCliSettings: "任务 CLI 设置",
    taskCliHint: "选择 AuraBot 做任务时使用哪个命令行 agent。优先级数字越小越先用。",
    taskRunner: "任务执行器",
    taskRunnerPriority: "任务执行器优先级",
    enable: "启用",
    priority: "优先级",
    petQuickControls: "桌宠快捷控制",
    dragPet: "拖动桌宠",
    drag: "拖动",
    startVoiceInput: "开始语音输入",
    stopVoiceRecording: "停止语音录制",
    voice: "语音",
    openAuraBotConsole: "打开 AuraBot 控制台",
    console: "控制台",
    openAuraBotSettings: "打开 AuraBot 设置",
    petQuickChat: "桌宠快捷聊天",
    petChatPlaceholder: "给主对话发送消息",
    sendPetChat: "发送桌宠消息",
    openPetQuickChat: "打开桌宠快捷聊天",
    chat: "聊天",
    petMessage: "桌宠消息",
    desktopLive2DPet: "桌面 Live2D 桌宠",
    interactWithAuraBot: "和 AuraBot 互动",
    memoryDebugTools: "记忆调试工具",
    memoryDebug: "记忆调试",
    refresh: "刷新",
    memoryDebugPanel: "记忆调试面板",
    memoryDebugMetrics: "记忆调试指标",
    nodes: "节点",
    edges: "边",
    summaries: "摘要",
    events: "事件",
    recentEvents: "最近事件",
    noMemorySnapshot: "还没有加载记忆快照。",
    lastMemoryRecallTrace: "最近一次记忆召回轨迹",
    lastRecall: "最近召回",
    seeds: "种子",
    expanded: "扩展",
    returned: "返回",
    taskStatus: "任务状态",
    cancel: "取消",
    delete: "删除",
    cancelTask: "取消任务",
    deleteTask: "删除任务"
  }
} as const;

const AGENT_STATUS_LABELS: Record<DisplayLanguage, Record<AgentStatus, string>> = {
  en: {
    idle: "idle",
    listening: "listening",
    thinking: "thinking",
    typing: "typing",
    tasking: "tasking"
  },
  zh: {
    idle: "空闲中",
    listening: "倾听中",
    thinking: "判断中",
    typing: "对方正在输入中",
    tasking: "任务中"
  }
};

const TASK_STATUS_LABELS: Record<DisplayLanguage, Record<Task["status"], string>> = {
  en: {
    queued: "queued",
    running: "running",
    waiting: "waiting",
    completed: "completed",
    failed: "failed",
    interrupted: "stopped"
  },
  zh: {
    queued: "待开始",
    running: "处理中",
    waiting: "处理中",
    completed: "完成",
    failed: "没跑成",
    interrupted: "已停下"
  }
};

export type UiTextKey = keyof typeof UI_TEXT.en;

export function t(displayLanguage: DisplayLanguage | undefined, key: UiTextKey): string {
  return UI_TEXT[displayLanguage ?? "en"][key];
}

export function agentStatusLabel(displayLanguage: DisplayLanguage | undefined, status: AgentStatus): string {
  return AGENT_STATUS_LABELS[displayLanguage ?? "en"][status];
}

export function taskStatusLabel(displayLanguage: DisplayLanguage | undefined, status: Task["status"]): string {
  return TASK_STATUS_LABELS[displayLanguage ?? "en"][status];
}

export function taskProgressLabel(displayLanguage: DisplayLanguage | undefined, task: Task): string {
  const language = displayLanguage ?? "en";

  if (language === "zh") {
    if (task.status === "queued") {
      return "还没开始";
    }

    if (task.status === "completed") {
      return "结果已经准备好";
    }

    if (task.status === "failed") {
      return "遇到一点问题";
    }

    if (task.status === "interrupted") {
      return "已经停下";
    }

    return cleanTaskProgress(task.latestProgress, language);
  }

  if (task.status === "queued") {
    return "Not started yet";
  }

  if (task.status === "completed") {
    return "Result is ready";
  }

  if (task.status === "failed") {
    return "Needs attention";
  }

  if (task.status === "interrupted") {
    return "Stopped";
  }

  return cleanTaskProgress(task.latestProgress, language);
}

function cleanTaskProgress(progress: string, displayLanguage: DisplayLanguage): string {
  const fallback = displayLanguage === "zh" ? "正在处理" : "Working";
  const cleaned = progress
    .replace(/^(Codex|Claude Code|Hermes) 子任务会话已建立：.*$/u, displayLanguage === "zh" ? "已经接上任务线程" : "Task thread connected")
    .replace(/^(Codex|Claude Code|Hermes) 子任务开始处理任务。?$/u, displayLanguage === "zh" ? "正在处理" : "Working")
    .replace(/^开始调用 (Codex|Claude Code|Hermes) 子任务：.*$/u, displayLanguage === "zh" ? "正在处理" : "Working")
    .replace(/^(Codex|Claude Code|Hermes) 子任务完成命令：.*$/u, displayLanguage === "zh" ? "刚完成一步处理" : "Completed one step")
    .replace(/^(Codex|Claude Code|Hermes) 子任务(?:新增|修改|删除)文件：.*$/u, displayLanguage === "zh" ? "正在整理结果文件" : "Preparing result files")
    .replace(/^(Codex|Claude Code|Hermes) 子任务本轮处理完成，正在收尾。?$/u, displayLanguage === "zh" ? "正在收尾" : "Wrapping up")
    .trim();

  return cleaned || fallback;
}
