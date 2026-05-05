import { describe, expect, it } from "vitest";
import {
  DeterministicVoiceAdapter,
  ModelBackedVoiceAdapter
} from "../../../../src/core/voice/voiceAdapter";
import type { ChatCompletionRequest } from "../../../../src/core/llm/openAiCompatibleClient";
import type { Soul } from "../../../../src/shared/schemas/profile";

const soul: Soul = {
  id: "soul_default",
  profileId: "profile_default",
  name: "Aura",
  persona: "伴侣兼助手，亲近、可靠，会自然陪用户推进事情。",
  tone: "亲近、轻松、短句、自然；完成事情时像本人把结果告诉用户。",
  principles: ["任务完成后可以自然邀请用户看一眼，但不要固定套一句样例。"],
  taboos: ["不要用客服工单、系统通知、运行日志或任务广播的语义口吻。"],
  speakingStyle: "短句、自然、有温度。",
  relationship: "长期陪伴用户的桌面伴侣和助手。",
  boundaries: ["不要把不确定的记忆说成确定事实。"],
  topicPreferences: ["tasks"],
  createdAt: "2026-05-05T00:00:00.000Z",
  updatedAt: "2026-05-05T00:00:00.000Z"
};

describe("VoiceAdapter", () => {
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

    expect(reply).toContain("GitHub 周报");
    expect(reply).toContain("文件在下面");
    expect(reply).toContain("看一眼");
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

    expect(reply).toContain("面试清单");
    expect(reply).not.toContain("文件");
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
      },
      fallbackText: "周报写好了。"
    });

    expect(reply).toBe("周报弄好了，文件我放在下面。你要不要先看一眼？");
    expect(request?.messages[0]?.content).toContain("不要固定套用任何一句样例");
    expect(request?.messages[1]?.content).toContain("完成事情时像本人把结果告诉用户");
    expect(request?.messages[1]?.content).toContain("任务完成后可以自然邀请用户看一眼");
    expect(request?.messages[1]?.content).toContain("hasAttachments");
  });
});

