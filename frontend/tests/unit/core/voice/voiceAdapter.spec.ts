/**
 * VoiceAdapter 单元测试：覆盖保留的 Memory/idle 确定性措辞与模型适配路径。
 * 测试确保输入只包含陪伴型场景，且模型响应清洗和配置透传保持稳定。
 */
import { describe, expect, it, vi } from "vitest";
import {
  DeterministicVoiceAdapter,
  ModelBackedVoiceAdapter,
  createVoiceAdapter
} from "@core/voice/voiceAdapter";

describe("VoiceAdapter", () => {
  it("renders a memory follow-up from authoritative contents", async () => {
    const adapter = new DeterministicVoiceAdapter();
    await expect(adapter.render({
      scene: "memory.follow_up",
      facts: { contents: ["需要跟进或提醒用户：确认报名"] }
    })).resolves.toBe("我提醒你一下：确认报名");
  });

  it("renders the retained idle nudge", async () => {
    const adapter = new DeterministicVoiceAdapter();
    await expect(adapter.render({ scene: "proactive.idle_nudge", facts: {} })).resolves.toContain("我在这儿");
  });

  it("uses the configured model and sanitizes fenced text", async () => {
    const complete = vi.fn().mockResolvedValue("```text\n轻轻提醒你一下。\n```");
    const adapter = new ModelBackedVoiceAdapter({
      client: { complete },
      config: {
        provider: "openai-compatible",
        enabled: true,
        baseUrl: "https://example.test/v1",
        apiKey: "secret",
        model: "voice-test",
        temperature: 0.2
      }
    });

    await expect(adapter.render({ scene: "memory.follow_up", facts: { contents: ["看看报名"] } }))
      .resolves.toBe("轻轻提醒你一下。");
    expect(complete).toHaveBeenCalledWith(expect.objectContaining({ model: "voice-test" }));
  });

  it("creates a deterministic adapter for the test stub", () => {
    vi.stubEnv("AURABOT_DETERMINISTIC_MODEL_FALLBACK", "1");
    try {
      expect(createVoiceAdapter({ provider: "local-stub", enabled: true, model: "stub" }))
        .toBeInstanceOf(DeterministicVoiceAdapter);
    } finally {
      vi.unstubAllEnvs();
    }
  });
});
