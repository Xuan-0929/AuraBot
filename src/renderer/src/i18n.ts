import type { AgentStatus } from "../../shared/contracts/chat";
import type { PetSettings } from "../../shared/schemas/petSettings";
import type { Task } from "../../shared/schemas/task";

export type DisplayLanguage = PetSettings["displayLanguage"];

const UI_TEXT = {
  en: {
    appTitle: "AuraBot",
    consoleTitle: "AuraBot Console",
    consoleSubtitle: "When you want to talk properly, we can settle in here.",
    mainConversation: "Main conversation",
    conversationMessages: "Conversation messages",
    readyWhenYouAre: "I'm here. What feels easiest to start with today?",
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
    settingsWindowEyebrow: "Aura's little room",
    productSettingsSubtitle: "Settle this little room until Aura feels right beside you.",
    settingsNavigation: "Settings navigation",
    settingsContent: "Settings content",
    settingsSidebarHint: "Pick a corner on the left, then settle one thing at a time.",
    general: "General",
    generalSettingsHint: "Start with the everyday basics that make the room feel easy to live in.",
    petBehavior: "Pet Behavior",
    petBehaviorSettingsHint: "Tweak the small habits that shape how Aura stays around your desk.",
    language: "Language",
    english: "English",
    chinese: "中文",
    model: "Model",
    modelSettings: "Model settings",
    modelSettingsHint: "Keep the engines in the back room tidy here; the exact keys and model names still live in config.json.",
    configNotLoaded: "Config not loaded",
    openConfigFile: "Open config file",
    character: "Character",
    characterSettings: "Character settings",
    persona: "Persona",
    live2dModel: "Live2D Model",
    live2dModelSettingsHint: "Pick the look Aura wears in this room and preview the one already with you.",
    notifications: "Notifications",
    notificationSettingsHint: "Choose which little taps on the shoulder Aura is allowed to send.",
    notificationAppRunningOnlyHint: "For now, these notifications only work while AuraBot is already running.",
    memoryDebugSettingsHint: "Only drop into this page when you need to inspect what Aura's memory is carrying behind the scenes.",
    personaSettingsHint: "Shape how Aura sounds and feels, then open the deeper notes only when you need them.",
    personaSettingsComingSoon: "Persona editing will land here in this phase.",
    live2dModelSettingsComingSoon: "Live2D model preview and switching will land here in this phase.",
    live2dCurrentModel: "Current model",
    live2dModelPath: "Model path",
    live2dLocalModels: "Local models",
    systemNotifications: "System notifications",
    enableSystemNotifications: "Enable system notifications",
    notificationPermission: "Permission",
    notificationPermissionSystemManaged: "Managed by system",
    notificationPermissionUnsupported: "Unsupported in this environment",
    followUpNotifications: "Follow-up reminders",
    enableFollowUpNotifications: "Enable follow-up reminders",
    timeReminderNotifications: "Time reminder tasks",
    enableTimeReminderNotifications: "Enable time reminder task notifications",
    idleNudgeNotifications: "Idle nudges",
    enableIdleNudgeNotifications: "Enable idle nudge notifications",
    personaName: "Name",
    personaDescription: "Description",
    personaTone: "Tone",
    personaRelationship: "Relationship",
    personaProactiveLevel: "Proactive level",
    personaProactiveLow: "Low",
    personaProactiveBalanced: "Balanced",
    personaProactiveHigh: "High",
    personaPrinciples: "Principles",
    personaTaboos: "Taboos",
    personaReminderStyle: "Reminder style",
    personaReplyPreference: "Reply preference",
    openAdvancedPersonaEditing: "Open advanced persona editing",
    closeAdvancedPersonaEditing: "Close advanced persona editing",
    savePersonaSettings: "Save persona settings",
    clickThroughCharacter: "Click through character",
    provider: "Provider",
    providerOpenAICompatible: "OpenAI compatible",
    providerLangChainOpenAI: "LangChain OpenAI",
    modelHealth: "Status",
    testConnection: "Test connection",
    modelStateNotConfigured: "Not configured",
    modelStateConfigured: "Configured",
    modelStateTesting: "Testing…",
    modelStateConnected: "Connected",
    modelStateError: "Connection failed",
    modelStateUsingTestDouble: "Test double",
    modelErrorMissingConfiguration: "Model configuration is incomplete.",
    modelErrorUnsupportedProvider: "Unsupported model provider.",
    modelErrorAuthenticationFailed: "API key is invalid or lacks permission.",
    modelErrorTimeout: "Model request timed out.",
    modelErrorNetwork: "Could not reach the model service.",
    modelErrorMalformedResponse: "Model response format was invalid.",
    modelErrorInvocationFailed: "Model request failed.",
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
    taskCliHint: "Choose which command-line helper Aura should quietly call in when a task needs hands.",
    taskRunner: "task runner",
    taskRunnerPriority: "task runner priority",
    proactiveMessages: "Proactive messages",
    proactiveMessagesHint: "Set how long Aura waits before a light idle message.",
    proactiveWaitMinutes: "Wait minutes",
    proactiveMessageWaitMinutes: "Proactive message wait minutes",
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
    taskStickyNotes: "Today's notes",
    taskStickyNoteHint: "I'll keep the important bits lightly pinned beside us.",
    cancel: "Cancel",
    delete: "Delete",
    cancelTask: "Cancel task",
    deleteTask: "Delete task"
  },
  zh: {
    appTitle: "AuraBot",
    consoleTitle: "AuraBot 控制台",
    consoleSubtitle: "想认真聊的时候，我们就在这里慢慢说。",
    mainConversation: "主对话",
    conversationMessages: "对话消息",
    readyWhenYouAre: "我在，今天想先从哪件事开始？",
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
    settingsWindowEyebrow: "Aura 的小屋",
    productSettingsSubtitle: "把这间小屋慢慢收拾成你和 Aura 都住得舒服的样子。",
    settingsNavigation: "设置导航",
    settingsContent: "设置内容",
    settingsSidebarHint: "左边这些角落不变，我们只是把它收拾得更像住人的地方。",
    general: "通用",
    generalSettingsHint: "先把这间小屋最常用的习惯放顺手。",
    petBehavior: "桌宠行为",
    petBehaviorSettingsHint: "调整 Aura 待在桌边时那些会被你天天感受到的小动作。",
    language: "语言",
    english: "English",
    chinese: "中文",
    model: "模型",
    modelSettings: "模型设置",
    modelSettingsHint: "把后台那几盏灯和连线照看好，聊天、记忆和任务就会更稳；更细的线路仍然放在 config.json 里。",
    configNotLoaded: "配置未加载",
    openConfigFile: "打开 config 文件",
    character: "人物",
    characterSettings: "人物设置",
    persona: "人格",
    live2dModel: "Live2D 模型",
    live2dModelSettingsHint: "看看现在陪着你的样子，也挑一套更适合这间小屋的外观。",
    notifications: "通知",
    notificationSettingsHint: "决定 Aura 可以用哪些轻轻提醒你的方式。",
    notificationAppRunningOnlyHint: "目前这些系统通知只在 AuraBot 已经运行时生效。",
    memoryDebugSettingsHint: "只有在你真想检查 Aura 现在记住了什么时，才需要翻到这一页。",
    personaSettingsHint: "把 Aura 的说话感觉和相处方式慢慢调顺，需要时再展开更深一点的设定。",
    personaSettingsComingSoon: "这一期会把人格编辑收进这里。",
    live2dModelSettingsComingSoon: "这一期会把 Live2D 模型预览和切换收进这里。",
    live2dCurrentModel: "当前模型",
    live2dModelPath: "模型路径",
    live2dLocalModels: "本机模型",
    systemNotifications: "系统通知",
    enableSystemNotifications: "启用系统通知",
    notificationPermission: "权限状态",
    notificationPermissionSystemManaged: "由系统管理",
    notificationPermissionUnsupported: "当前环境不支持",
    followUpNotifications: "Follow-up 提醒",
    enableFollowUpNotifications: "启用 Follow-up 提醒通知",
    timeReminderNotifications: "时间提醒任务",
    enableTimeReminderNotifications: "启用时间提醒任务通知",
    idleNudgeNotifications: "空闲主动提醒",
    enableIdleNudgeNotifications: "启用空闲主动提醒通知",
    personaName: "名称",
    personaDescription: "简介",
    personaTone: "语气",
    personaRelationship: "关系感",
    personaProactiveLevel: "主动程度",
    personaProactiveLow: "低",
    personaProactiveBalanced: "中",
    personaProactiveHigh: "高",
    personaPrinciples: "原则",
    personaTaboos: "禁忌",
    personaReminderStyle: "提醒风格",
    personaReplyPreference: "回复偏好",
    openAdvancedPersonaEditing: "进入高级编辑",
    closeAdvancedPersonaEditing: "收起高级编辑",
    savePersonaSettings: "保存人格设置",
    clickThroughCharacter: "穿透人物点击",
    provider: "服务商",
    providerOpenAICompatible: "OpenAI compatible",
    providerLangChainOpenAI: "LangChain OpenAI",
    modelHealth: "状态",
    testConnection: "测试连接",
    modelStateNotConfigured: "未配置",
    modelStateConfigured: "已配置",
    modelStateTesting: "测试中",
    modelStateConnected: "已连接",
    modelStateError: "连接失败",
    modelStateUsingTestDouble: "测试替身",
    modelErrorMissingConfiguration: "模型配置不完整。",
    modelErrorUnsupportedProvider: "模型服务商不受支持。",
    modelErrorAuthenticationFailed: "API Key 无效或权限不足。",
    modelErrorTimeout: "模型请求超时。",
    modelErrorNetwork: "无法连接到模型服务。",
    modelErrorMalformedResponse: "模型返回格式不正确。",
    modelErrorInvocationFailed: "模型请求失败。",
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
    taskCliHint: "选择 Aura 遇到任务时要悄悄叫哪位命令行搭子来帮忙。",
    taskRunner: "任务执行器",
    taskRunnerPriority: "任务执行器优先级",
    proactiveMessages: "主动消息",
    proactiveMessagesHint: "设置 Aura 空闲多久后轻轻主动冒泡。",
    proactiveWaitMinutes: "等待分钟数",
    proactiveMessageWaitMinutes: "主动消息等待分钟数",
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
    taskStickyNotes: "今日便签",
    taskStickyNoteHint: "我先替你轻轻记在旁边。",
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
    if (task.kind === "time_reminder") {
      if (task.status === "queued" && task.dueAt) {
        return `会在 ${formatTaskDueTime(task.dueAt, language)} 提醒`;
      }

      if (task.status === "completed") {
        return "已经提醒过";
      }
    }

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
    if (task.kind === "time_reminder" && task.dueAt) {
      return `Reminds at ${formatTaskDueTime(task.dueAt, language)}`;
    }

    return "Not started yet";
  }

  if (task.kind === "time_reminder" && task.status === "completed") {
    return "Reminder sent";
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

function formatTaskDueTime(value: string, language: DisplayLanguage): string {
  const date = new Date(value);

  if (!Number.isFinite(date.getTime())) {
    return value;
  }

  return date.toLocaleTimeString(language === "zh" ? "zh-CN" : "en", {
    hour: "2-digit",
    minute: "2-digit"
  });
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
