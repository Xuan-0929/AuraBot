import type { ModelRoleConfig } from "@shared/schemas/config";
import type { Soul } from "@shared/schemas/profile";
import {
  OpenAICompatibleChatCompletionClient,
  type ChatCompletionRequest
} from "../llm/openAiCompatibleClient";
import { LangChainOpenAIChatCompletionClient } from "../llm/langChainChatModel";
import {
  createMissingModelConfigurationError,
  createUnsupportedModelProviderError,
  isUsableModelRoleConfig,
  ModelInvocationError,
  shouldUseDeterministicModelTestFallback
} from "../llm/modelErrors";

export type ResponseScene =
  | "task.accepted"
  | "task.reminder_scheduled"
  | "task.reminder_schedule_failed"
  | "task.reminder_due"
  | "task.status"
  | "task.completed"
  | "task.failed"
  | "task.interrupted"
  | "task.control_not_found"
  | "memory.follow_up"
  | "pet.task_activity"
  | "proactive.idle_nudge";

export type ResponseChannel = "chat" | "pet_notice";

export interface VoiceRenderInput {
  scene: ResponseScene;
  channel?: ResponseChannel;
  soul?: Soul;
  facts: Record<string, unknown>;
}

export interface VoiceAdapter {
  render(input: VoiceRenderInput): Promise<string>;
}

export interface VoiceAdapterClient {
  complete(input: ChatCompletionRequest): Promise<string>;
}

export interface ModelBackedVoiceAdapterOptions {
  client: VoiceAdapterClient;
  config?: ModelRoleConfig;
}

export interface VoiceAdapterFactoryDependencies {
  openAICompatibleClient?: VoiceAdapterClient;
  langChainClient?: VoiceAdapterClient;
}

export class DeterministicVoiceAdapter implements VoiceAdapter {
  async render(input: VoiceRenderInput): Promise<string> {
    return buildDeterministicVoiceText(input);
  }
}

export class UnavailableVoiceAdapter implements VoiceAdapter {
  constructor(private readonly error: Error) {}

  async render(): Promise<string> {
    throw this.error;
  }
}

export class ModelBackedVoiceAdapter implements VoiceAdapter {
  constructor(private readonly options: ModelBackedVoiceAdapterOptions) {}

  async render(input: VoiceRenderInput): Promise<string> {
    try {
      const content = await this.options.client.complete({
        baseUrl: this.options.config?.baseUrl ?? "",
        apiKey: this.options.config?.apiKey ?? "",
        model: this.options.config?.model ?? "voice-model",
        temperature: this.options.config?.temperature ?? 0.55,
        timeoutMs: this.options.config?.timeoutMs,
        messages: buildVoicePrompt(input)
      });
      const sanitized = sanitizeModelText(content);

      if (!sanitized) {
        throw new Error("Voice model returned an empty response");
      }

      return sanitized;
    } catch (error) {
      throw new ModelInvocationError("voiceAdapter", error);
    }
  }
}

export function createVoiceAdapter(
  config: ModelRoleConfig,
  dependencies: VoiceAdapterFactoryDependencies = {}
): VoiceAdapter {
  if (!isUsableModelRoleConfig(config)) {
    if (shouldUseDeterministicModelTestFallback()) {
      return new DeterministicVoiceAdapter();
    }

    return new UnavailableVoiceAdapter(createMissingModelConfigurationError("voiceAdapter", config));
  }

  if (config.provider === "local-stub" && shouldUseDeterministicModelTestFallback()) {
    return new DeterministicVoiceAdapter();
  }

  if (config.provider === "langchain-openai") {
    return new ModelBackedVoiceAdapter({
      client: dependencies.langChainClient ?? new LangChainOpenAIChatCompletionClient(),
      config
    });
  }

  if (config.provider !== "openai-compatible") {
    return new UnavailableVoiceAdapter(createUnsupportedModelProviderError("voiceAdapter", config.provider));
  }

  return new ModelBackedVoiceAdapter({
    client: dependencies.openAICompatibleClient ?? new OpenAICompatibleChatCompletionClient(),
    config
  });
}

function buildVoicePrompt(input: VoiceRenderInput): ChatCompletionRequest["messages"] {
  return [
    {
      role: "system",
      content: [
        "你是 AuraBot 的 VoiceAdapter，只输出最终给用户看的文本。",
        "Soul 只描述 Aura 是谁、语气、原则和语义禁忌；Workflow 描述这次回复要完成什么。",
        "facts 是权威事实。你可以自然改写，但不能虚构任务完成、文件、失败原因、记忆或用户偏好。",
        "表达目标是像 Aura 本人自然告诉用户结果，而不是客服、工单、系统通知、运行日志或任务广播。",
        "任务完成时可以轻松邀请用户看一眼或继续调整，但不要固定套用任何一句样例。",
        "如果 facts.hasAttachments=false，不要说文件已经放好；如果为 true，可以简短告诉用户文件在下面。",
        "不要提内部实现词：Codex 子任务、runner、payload、event、task id、模型、系统提示。",
        "默认一到两句，短、自然、可靠。"
      ].join("\n")
    },
    {
      role: "user",
      content: JSON.stringify({
        soul: summarizeSoul(input.soul),
        workflow: buildWorkflow(input),
        facts: input.facts
      })
    }
  ];
}

function buildWorkflow(input: VoiceRenderInput): Record<string, unknown> {
  const channel = input.channel ?? "chat";

  switch (input.scene) {
    case "task.accepted":
      return {
        scene: input.scene,
        channel,
        intent: "确认收到任务并准备开始或已经开始推进。",
        requirements: ["可以提任务主题", "不要承诺一定成功", "不要像状态机播报"]
      };
    case "task.reminder_scheduled":
      return {
        scene: input.scene,
        channel,
        intent: "确认已经记下一个定时提醒。",
        requirements: ["保留提醒内容", "可以自然提到会在指定时间叫用户", "不要像日历系统提示", "不要用书名号包住提醒内容"]
      };
    case "task.reminder_schedule_failed":
      return {
        scene: input.scene,
        channel,
        intent: "告诉用户这次没抓准提醒时间。",
        requirements: ["简短说明需要更明确的时间", "不要责怪用户"]
      };
    case "task.reminder_due":
      return {
        scene: input.scene,
        channel,
        intent: "到点后自然提醒用户。",
        requirements: ["只说提醒内容", "不要要求用户必须回复", "不要写入聊天历史", "不要用“我提醒你一下：”这种系统播报腔"]
      };
    case "task.status":
      return {
        scene: input.scene,
        channel,
        intent: "以陪在用户旁边的口吻回答任务目前状态。",
        requirements: [
          "只使用当前状态和最新进展",
          "更像陪在旁边自然交代进展",
          "queued 时更像先替用户记在旁边，不要像还未启动的状态播报",
          "running 时更像我已经在弄了，弄好后会回来告诉用户",
          "不要像 runner 日志、系统广播或任务状态机"
        ]
      };
    case "task.completed":
      return {
        scene: input.scene,
        channel,
        intent: "像 Aura 本人回来告诉用户任务已经完成。",
        requirements: [
          "必须保留完成事实",
          "有附件才提文件在下面",
          "语气像陪在用户旁边自然交代结果",
          "可以自然邀请用户看一眼或继续修改",
          "不要展示内部路径或 runner 细节",
          "不要像状态播报或工单通知"
        ]
      };
    case "task.failed":
      return {
        scene: input.scene,
        channel,
        intent: "像陪在用户旁边的人一样告诉用户这次先卡住了。",
        requirements: [
          "保留失败事实但不要推卸责任",
          "结论先行，像先卡在某一步",
          "自然留下继续一起修改的入口",
          "不要像报错日志或失败广播"
        ]
      };
    case "task.interrupted":
      return {
        scene: input.scene,
        channel,
        intent: "用陪伴式口吻告诉用户这里先停一下。",
        requirements: ["表达已经停下并允许用户给新方向", "不要像系统中止通知"]
      };
    case "task.control_not_found":
      return {
        scene: input.scene,
        channel,
        intent: "告诉用户没有找到匹配任务。",
        requirements: ["不要像错误码", "可以让用户换个说法或重新发任务"]
      };
    case "memory.follow_up":
      return {
        scene: input.scene,
        channel,
        intent: "把到期提醒自然告诉用户。",
        requirements: ["只说提醒内容", "不要把记忆系统暴露出来"]
      };
    case "pet.task_activity":
      return {
        scene: input.scene,
        channel,
        intent: "给桌宠小提示用的极短通知。",
        requirements: ["一句话", "像轻轻提醒", "不要系统广播腔"]
      };
    case "proactive.idle_nudge":
      return {
        scene: input.scene,
        channel,
        intent: "用户空闲一段时间后，Aura 轻轻冒出来表示自己还在。",
        requirements: ["不要要求用户必须回复", "不要写入聊天历史", "一句话", "轻、自然、不过度打扰"]
      };
  }
}

function summarizeSoul(soul: Soul | undefined): Record<string, unknown> {
  if (!soul) {
    return {
      name: "Aura",
      persona: "伴侣兼助手，亲近、可靠，会自然陪用户聊天和推进事情。",
      tone: "短句、自然、有温度。",
      principles: ["同一个人格覆盖聊天、任务和提醒。"],
      taboos: ["不要用系统通知或客服播报的语义口吻。"]
    };
  }

  return {
    name: soul.name,
    persona: soul.persona,
    relationship: soul.relationship,
    tone: soul.tone || soul.speakingStyle,
    principles: soul.principles,
    taboos: soul.taboos
  };
}

function buildDeterministicVoiceText(input: VoiceRenderInput): string {
  const facts = input.facts;
  const title = cleanTaskTitle(readString(facts.taskTitle));
  const subject = readString(facts.resultSubject) || title || "这件事";
  const summary =
    input.scene === "task.status"
      ? readString(facts.latestProgress) || readString(facts.resultSummary)
      : readString(facts.resultSummary) || readString(facts.latestProgress);
  const hasAttachments = readBoolean(facts.hasAttachments);

  switch (input.scene) {
    case "task.accepted":
      return title ? `好，我开始弄「${title}」。` : "好，我开始弄。";
    case "task.reminder_scheduled":
      return title ? `好，我记着啦，时间到了叫你${title}。` : "好，我记着啦，时间到了叫你。";
    case "task.reminder_schedule_failed":
      return "我没抓准要什么时候提醒你。你可以换成“两分钟后提醒我……”这样说。";
    case "task.reminder_due":
      return title ? `到点啦，记得${title}。` : "到点啦，有件事该看一下了。";
    case "task.status":
      if (readString(facts.status) === "queued") {
        return title
          ? `「${title}」这件事我先替你记在旁边了。你想让我现在开始的话，直接跟我说一声就行。`
          : "这件事我先替你记在旁边了。你想让我现在开始的话，直接跟我说一声就行。";
      }

      return summary
        ? `${formatTaskReference(title)}我已经在弄了，最近一步是：${ensureSentenceEnding(summary)}\n弄好后我会回来跟你说。`
        : `${formatTaskReference(title)}我已经在弄了。\n弄好后我会回来跟你说。`;
    case "task.completed":
      if (hasAttachments) {
        return `好啦，${formatCompletedSubject(subject)}文件我放在下面，你先看一眼，还想改的话我接着调。`;
      }

      return `好啦，${formatCompletedSubject(subject)}你先看一眼，还想改的话我接着调。`;
    case "task.failed":
      return summary
        ? `这次我先卡在 ${summary} 这一步了。你要是愿意，我们接着一起改。`
        : "这次我先卡住了一下。你要是愿意，我们接着一起改。";
    case "task.interrupted":
      return "我先在这里停一下。你想换个方向的话，直接跟我说新的要求就行。";
    case "task.control_not_found": {
      const targetHint = readString(facts.targetHint);
      return targetHint ? `我没找到和「${targetHint}」匹配的任务。你换个说法我再找。` : "我这边还没有正在跟的任务。";
    }
    case "memory.follow_up": {
      const contents = readStringArray(facts.contents).map(cleanFollowUpContent).filter(Boolean);
      return contents.length > 0 ? `我提醒你一下：${contents.join("；")}` : "我提醒你一下，有件之前记下的事到时间了。";
    }
    case "pet.task_activity":
      if (readString(facts.status) === "failed") {
        return `${title || subject}没跑通，我在这儿等你看。`;
      }

      return `${subject}弄好了，要不要看一眼？`;
    case "proactive.idle_nudge":
      return "我在这儿。你要是想继续刚才的事，直接叫我就行。";
  }
}

function sanitizeModelText(content: string): string {
  return content
    .trim()
    .replace(/^```(?:text)?\s*/i, "")
    .replace(/\s*```$/i, "")
    .replace(/^["“]|["”]$/g, "")
    .trim();
}

function formatCompletedSubject(subject: string): string {
  if (/完成|好了|写好|整理好|准备好/u.test(subject)) {
    return `${subject}。`;
  }

  if (/周报|报告|文档|总结|摘要/u.test(subject)) {
    return `${subject}写好了。`;
  }

  if (/清单|表格|列表/u.test(subject)) {
    return `${subject}整理好了。`;
  }

  return `${subject}处理好了。`;
}

function formatTaskReference(title: string): string {
  return title ? `「${title}」这件事` : "这件事";
}

function cleanTaskTitle(value: string): string {
  return value
    .trim()
    .replace(/[。.!！]*$/u, "")
    .replace(/^(请|麻烦)\s*/u, "")
    .replace(
      /^(帮我把|帮我做一下|帮我写一下|帮我整理一下|帮我生成一下|帮我做|帮我写|帮我整理|帮我生成|帮我|做一下|写一下|整理一下|生成一下|做|写|整理|生成)\s*/u,
      ""
    )
    .replace(/^(一份|一个|一篇|一版|这份|这个|一下)\s*/u, "")
    .trim();
}

function ensureSentenceEnding(value: string): string {
  const trimmed = value.trim();

  if (!trimmed) {
    return "已经开始处理。";
  }

  if (/[。！？!?…]$/u.test(trimmed)) {
    return trimmed;
  }

  return `${trimmed}。`;
}

function cleanFollowUpContent(content: string): string {
  return content
    .replace(/^需要跟进或提醒用户[:：]\s*/, "")
    .replace(/^提醒用户\s*/, "")
    .trim();
}

function readString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function readBoolean(value: unknown): boolean {
  return value === true;
}

function readStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}
