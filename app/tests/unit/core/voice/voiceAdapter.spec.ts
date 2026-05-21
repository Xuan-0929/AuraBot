import { describe, expect, it } from "vitest";
import {
  createVoiceAdapter,
  DeterministicVoiceAdapter,
  ModelBackedVoiceAdapter
} from "../../../../packages/core-legacy/src/voice/voiceAdapter";
import { ModelConfigurationError, ModelInvocationError } from "../../../../packages/core-legacy/src/llm/modelErrors";
import type { ChatCompletionRequest } from "../../../../packages/core-legacy/src/llm/openAiCompatibleClient";
import type { Soul } from "../../../../packages/shared/src/schemas/profile";

const soul: Soul = {
  id: "soul_default",
  profileId: "profile_default",
  name: "Aura",
  persona: "伴侣兼助手，亲近、可靠，会自然陪用户推进事情。",
  tone: "亲近、轻松、短句、自然；完成事情时像本人把结果告诉用户。",
  proactiveLevel: "balanced",
  principles: ["任务完成后可以自然邀请用户看一眼，但不要固定套一句样例。"],
  taboos: ["不要用客服工单、系统通知、运行日志或任务广播的语义口吻。"],
  reminderStyle: "",
  replyPreference: "",
  speakingStyle: "短句、自然、有温度。",
  relationship: "长期陪伴用户的桌面伴侣和助手。",
  boundaries: ["不要把不确定的记忆说成确定事实。"],
  topicPreferences: ["tasks"],
  createdAt: "2026-05-05T00:00:00.000Z",
  updatedAt: "2026-05-05T00:00:00.000Z"
};

describe("VoiceAdapter", () => {
  it("does not create user-facing deterministic voice text when API config is missing", async () => {
    const adapter = createVoiceAdapter({
      enabled: false,
      provider: "openai-compatible",
      model: "voice-model"
    });

    await expect(adapter.render({
      scene: "task.reminder_due",
      channel: "pet_notice",
      soul,
      facts: {
        taskTitle: "喝水"
      }
    })).rejects.toBeInstanceOf(ModelConfigurationError);
  });

  it("keeps deterministic task completion warm and attachment-safe", async () => {
    const adapter = new DeterministicVoiceAdapter();
    const reply = await adapter.render({
      scene: "task.completed",
      channel: "chat",
      soul,
      facts: {
        taskTitle: "帮我写一份 GitHub 周报。",
        resultSubject: "GitHub 周报",
        hasAttachments: true
      }
    });

    expect(reply).toBe("好啦，GitHub 周报写好了。文件我放在下面，你先看一眼，还想改的话我接着调。");
    expect(reply).not.toContain("任务完成：");
    expect(reply).not.toContain("Codex");
  });

  it("does not mention files when completion facts have no attachments", async () => {
    const adapter = new DeterministicVoiceAdapter();
    const reply = await adapter.render({
      scene: "task.completed",
      channel: "chat",
      soul,
      facts: {
        taskTitle: "帮我整理面试清单。",
        resultSubject: "面试清单",
        hasAttachments: false
      }
    });

    expect(reply).toBe("好啦，面试清单整理好了。你先看一眼，还想改的话我接着调。");
    expect(reply).not.toContain("文件");
  });

  it("keeps deterministic task status companion-first for queued and running updates", async () => {
    const adapter = new DeterministicVoiceAdapter();
    const queued = await adapter.render({
      scene: "task.status",
      channel: "chat",
      soul,
      facts: {
        taskTitle: "帮我做一个 GitHub 周报。",
        status: "queued",
        latestProgress: "Task queued from main chat."
      }
    });
    const running = await adapter.render({
      scene: "task.status",
      channel: "chat",
      soul,
      facts: {
        taskTitle: "帮我做一个 GitHub 周报。",
        status: "running",
        latestProgress: "正在整理本周合并记录"
      }
    });

    expect(queued).toBe("「GitHub 周报」这件事我先替你记在旁边了。你想让我现在开始的话，直接跟我说一声就行。");
    expect(running).toBe("「GitHub 周报」这件事我已经在弄了，最近一步是：正在整理本周合并记录。\n弄好后我会回来跟你说。");
  });

  it("keeps deterministic failed replies gentle while preserving the failure detail", async () => {
    const adapter = new DeterministicVoiceAdapter();
    const reply = await adapter.render({
      scene: "task.failed",
      channel: "chat",
      soul,
      facts: {
        taskTitle: "帮我做一份 GitHub 周报。",
        resultSummary: "codex exited 1"
      }
    });

    expect(reply).toBe("这次我先卡在 codex exited 1 这一步了。你要是愿意，我们接着一起改。");
    expect(reply).not.toContain("没跑通");
  });

  it("keeps deterministic interrupted replies aligned with the companion-first tone", async () => {
    const adapter = new DeterministicVoiceAdapter();
    const reply = await adapter.render({
      scene: "task.interrupted",
      channel: "chat",
      soul,
      facts: {
        taskTitle: "帮我做一份 GitHub 周报。"
      }
    });

    expect(reply).toBe("我先在这里停一下。你想换个方向的话，直接跟我说新的要求就行。");
    expect(reply).not.toContain("停下来了");
  });

  it("keeps deterministic reminder wording natural", async () => {
    const adapter = new DeterministicVoiceAdapter();
    const scheduled = await adapter.render({
      scene: "task.reminder_scheduled",
      channel: "chat",
      soul,
      facts: {
        taskTitle: "喝水",
        dueAt: "2026-05-06T04:02:00.000Z"
      }
    });
    const due = await adapter.render({
      scene: "task.reminder_due",
      channel: "pet_notice",
      soul,
      facts: {
        taskTitle: "喝水",
        dueAt: "2026-05-06T04:02:00.000Z"
      }
    });

    expect(scheduled).toContain("喝水");
    expect(scheduled).not.toContain("「");
    expect(scheduled).not.toContain("」");
    expect(due).toContain("喝水");
    expect(due).not.toContain("我提醒你一下：");
  });

  it("uses the LangChain chat provider for voice adaptation when configured", async () => {
    let request: ChatCompletionRequest | undefined;
    const adapter = createVoiceAdapter(
      {
        enabled: true,
        provider: "langchain-openai",
        baseUrl: "https://example.test/v1",
        apiKey: "key",
        model: "voice-model",
        temperature: 0.55
      },
      {
        langChainClient: {
          complete: async (input) => {
            request = input;

            return "好，我记着啦，等会儿叫你喝水。";
          }
        }
      }
    );

    const reply = await adapter.render({
      scene: "task.reminder_scheduled",
      channel: "chat",
      soul,
      facts: {
        taskTitle: "喝水",
        dueAt: "2026-05-06T04:02:00.000Z"
      }
    });

    expect(reply).toBe("好，我记着啦，等会儿叫你喝水。");
    expect(request).toMatchObject({
      baseUrl: "https://example.test/v1",
      apiKey: "key",
      model: "voice-model",
      temperature: 0.55
    });
  });

  it("passes soul, workflow, and facts to the model without using the example as a fixed template", async () => {
    let request: ChatCompletionRequest | undefined;
    const adapter = new ModelBackedVoiceAdapter({
      client: {
        complete: async (input) => {
          request = input;

          return "周报弄好了，文件我放在下面。你要不要先看一眼？";
        }
      },
      config: {
        enabled: true,
        provider: "openai-compatible",
        baseUrl: "https://example.test/v1",
        apiKey: "key",
        model: "voice-model"
      }
    });

    const reply = await adapter.render({
      scene: "task.completed",
      channel: "chat",
      soul,
      facts: {
        taskTitle: "帮我写一份周报。",
        resultSubject: "周报",
        hasAttachments: true
      }
    });

    expect(reply).toBe("周报弄好了，文件我放在下面。你要不要先看一眼？");
    expect(request?.messages[0]?.content).toContain("不要固定套用任何一句样例");
    expect(request?.messages[1]?.content).toContain("完成事情时像本人把结果告诉用户");
    expect(request?.messages[1]?.content).toContain("任务完成后可以自然邀请用户看一眼");
    expect(request?.messages[1]?.content).toContain("陪在用户旁边");
    expect(request?.messages[1]?.content).toContain("hasAttachments");
    expect(request?.messages[1]?.content).not.toContain("fallbackText");
  });

  it.each([
    {
      scene: "task.status" as const,
      facts: {
        taskTitle: "帮我做一个 GitHub 周报。",
        status: "running",
        latestProgress: "正在整理本周合并记录"
      },
      expectedWorkflowSnippets: [
        "\"scene\":\"task.status\"",
        "以陪在用户旁边的口吻回答任务目前状态",
        "更像陪在旁边自然交代进展",
        "queued 时更像先替用户记在旁边",
        "running 时更像我已经在弄了",
        "不要像 runner 日志、系统广播或任务状态机"
      ]
    },
    {
      scene: "task.failed" as const,
      facts: {
        taskTitle: "帮我做一份 GitHub 周报。",
        resultSummary: "codex exited 1"
      },
      expectedWorkflowSnippets: [
        "\"scene\":\"task.failed\"",
        "告诉用户这次先卡住了",
        "保留失败事实但不要推卸责任",
        "自然留下继续一起修改的入口",
        "不要像报错日志或失败广播"
      ]
    },
    {
      scene: "task.interrupted" as const,
      facts: {
        taskTitle: "帮我做一份 GitHub 周报。"
      },
      expectedWorkflowSnippets: [
        "\"scene\":\"task.interrupted\"",
        "用陪伴式口吻告诉用户这里先停一下",
        "表达已经停下并允许用户给新方向",
        "不要像系统中止通知"
      ]
    }
  ])("passes companion-first model-backed prompt contracts for $scene", async ({
    scene,
    facts,
    expectedWorkflowSnippets
  }) => {
    let request: ChatCompletionRequest | undefined;
    const adapter = new ModelBackedVoiceAdapter({
      client: {
        complete: async (input) => {
          request = input;

          return "收到。";
        }
      },
      config: {
        enabled: true,
        provider: "openai-compatible",
        baseUrl: "https://example.test/v1",
        apiKey: "key",
        model: "voice-model"
      }
    });

    const reply = await adapter.render({
      scene,
      channel: "chat",
      soul,
      facts
    });

    expect(reply).toBe("收到。");
    expect(request?.messages[0]?.content).toContain("而不是客服、工单、系统通知、运行日志或任务广播");
    expect(request?.messages[0]?.content).toContain("facts 是权威事实");
    for (const snippet of expectedWorkflowSnippets) {
      expect(request?.messages[1]?.content).toContain(snippet);
    }
  });

  it("propagates voice model failures instead of rendering deterministic task notices", async () => {
    const adapter = new ModelBackedVoiceAdapter({
      client: {
        complete: async () => {
          throw new Error("voice unavailable");
        }
      },
      config: {
        enabled: true,
        provider: "openai-compatible",
        baseUrl: "https://example.test/v1",
        apiKey: "key",
        model: "voice-model"
      }
    });

    await expect(adapter.render({
      scene: "task.reminder_due",
      channel: "pet_notice",
      soul,
      facts: {
        taskTitle: "喝水"
      }
    })).rejects.toBeInstanceOf(ModelInvocationError);
  });
});
