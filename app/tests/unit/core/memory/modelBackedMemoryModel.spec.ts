import { describe, expect, it } from "vitest";
import {
  createMemoryExtractor,
  ModelBackedMemoryExtractor,
  ModelBackedMemoryMergePlanner,
  ModelBackedMemoryRecallPlanner,
  ModelBackedMemoryRelationPlanner
} from "../../../../packages/core-legacy/src/memory/modelBackedMemoryModel";
import { ModelConfigurationError, ModelInvocationError } from "../../../../packages/core-legacy/src/llm/modelErrors";
import type { MemoryNode } from "../../../../packages/shared/src/schemas/memory";

const existingHotpotPreference: MemoryNode = {
  id: "memory_hotpot",
  profileId: "profile_default",
  type: "preference",
  state: "active",
  content: "用户喜欢火锅",
  importance: 0.72,
  createdAt: "2026-05-04T12:00:00.000+08:00",
  updatedAt: "2026-05-04T12:00:00.000+08:00"
};

describe("model-backed memory model", () => {
  it("does not fall back to deterministic memory extraction when API config is missing", async () => {
    const extractor = createMemoryExtractor({
      provider: "openai-compatible",
      model: "structured-memory-model",
      enabled: false
    });

    await expect(extractor.extract({
      message: "我喜欢吃火锅。",
      nowIso: "2026-05-04T12:00:00.000+08:00",
      timezone: "Asia/Shanghai"
    })).rejects.toBeInstanceOf(ModelConfigurationError);
  });

  it("creates memory extractors with a LangChain structured-output client when requested", async () => {
    const extractor = createMemoryExtractor(
      {
        provider: "langchain-openai",
        model: "structured-memory-model",
        enabled: true,
        baseUrl: "https://api.example.com/v1",
        apiKey: "test-key"
      },
      {
        langChainExtractionClient: {
          complete: async () =>
            JSON.stringify({
              candidates: [
                {
                  type: "trait",
                  content: "阿杰是用户的高中同学",
                  topic: "relationships",
                  importance: 0.74
                }
              ]
            })
        }
      }
    );

    const candidates = await extractor.extract({
      message: "阿杰是我高中同学。",
      nowIso: "2026-05-04T12:00:00.000+08:00",
      timezone: "Asia/Shanghai"
    });

    expect(candidates).toHaveLength(1);
    expect(candidates[0]?.node).toMatchObject({
      type: "trait",
      content: "阿杰是用户的高中同学"
    });
  });

  it("extracts structured memory candidates from model JSON", async () => {
    const extractor = new ModelBackedMemoryExtractor({
      client: {
        complete: async () =>
          JSON.stringify({
            candidates: [
              {
                type: "event",
                content: "用户明天要和阿杰去吃火锅",
                topic: "events",
                importance: 0.84,
                startsAt: "2026-05-05T19:00:00.000+08:00",
                confidence: 0.9
              },
              {
                type: "follow_up",
                content: "提醒用户明天和阿杰吃火锅",
                topic: "follow_ups",
                importance: 0.78,
                startsAt: "2026-05-05T18:00:00.000+08:00",
                relationToPrevious: {
                  relationType: "scheduled_for",
                  weight: 0.9
                }
              }
            ]
          })
      },
      config: {
        provider: "openai-compatible",
        model: "structured-memory-model",
        enabled: true,
        baseUrl: "https://api.example.com/v1",
        apiKey: "test-key"
      }
    });

    const candidates = await extractor.extract({
      message: "我明天要和阿杰去吃火锅，提醒我别忘了。",
      nowIso: "2026-05-04T12:00:00.000+08:00",
      timezone: "Asia/Shanghai"
    });

    expect(candidates).toHaveLength(2);
    expect(candidates[0]?.node).toMatchObject({
      type: "event",
      content: "用户明天要和阿杰去吃火锅",
      startsAt: "2026-05-05T19:00:00.000+08:00"
    });
    expect(candidates[1]?.relationToPrevious).toMatchObject({
      relationType: "scheduled_for",
      weight: 0.9
    });
  });

  it("propagates invalid model JSON instead of falling back to deterministic extraction", async () => {
    const extractor = new ModelBackedMemoryExtractor({
      client: {
        complete: async () => "不是 JSON"
      },
      config: {
        provider: "openai-compatible",
        model: "structured-memory-model",
        enabled: true,
        baseUrl: "https://api.example.com/v1",
        apiKey: "test-key"
      }
    });

    await expect(extractor.extract({
      message: "我喜欢吃火锅。",
      nowIso: "2026-05-04T12:00:00.000+08:00",
      timezone: "Asia/Shanghai"
    })).rejects.toBeInstanceOf(ModelInvocationError);
  });

  it("plans semantic merges against existing memory nodes", async () => {
    const planner = new ModelBackedMemoryMergePlanner({
      client: {
        complete: async () =>
          JSON.stringify({
            action: "update_existing",
            targetNodeId: "memory_hotpot",
            mergedContent: "用户喜欢火锅",
            importance: 0.82,
            reason: "same stable food preference"
          })
      },
      config: {
        provider: "openai-compatible",
        model: "structured-memory-model",
        enabled: true,
        baseUrl: "https://api.example.com/v1",
        apiKey: "test-key"
      }
    });

    const decision = await planner.plan({
      candidate: {
        type: "preference",
        content: "用户挺爱吃火锅",
        importance: 0.76
      },
      existingNodes: [existingHotpotPreference]
    });

    expect(decision).toMatchObject({
      action: "update_existing",
      targetNodeId: "memory_hotpot",
      mergedContent: "用户喜欢火锅",
      importance: 0.82
    });
  });

  it("keeps relation planner edges constrained to known target nodes", async () => {
    const planner = new ModelBackedMemoryRelationPlanner({
      client: {
        complete: async () =>
          JSON.stringify({
            edges: [
              {
                targetNodeId: "memory_hotpot",
                relationType: "preference_for",
                weight: 0.91,
                reason: "new event involves the user's hotpot preference"
              },
              {
                targetNodeId: "invented_id",
                relationType: "relates_to",
                weight: 0.7
              }
            ]
          })
      },
      config: {
        provider: "openai-compatible",
        model: "structured-memory-model",
        enabled: true,
        baseUrl: "https://api.example.com/v1",
        apiKey: "test-key"
      }
    });

    const edges = await planner.plan({
      newNode: {
        type: "event",
        content: "用户明天要和阿杰去吃火锅",
        importance: 0.84
      },
      existingNodes: [existingHotpotPreference]
    });

    expect(edges).toEqual([
      {
        targetNodeId: "memory_hotpot",
        relationType: "preference_for",
        weight: 0.91
      }
    ]);
  });

  it("plans recall terms and reranked node ids while filtering unknown nodes", async () => {
    const planner = new ModelBackedMemoryRecallPlanner({
      client: {
        complete: async () =>
          JSON.stringify({
            queryTerms: ["火锅", "吃的偏好"],
            targetTypes: ["preference"],
            rankedNodeIds: ["memory_hotpot", "invented_id"],
            reason: "the user asks what they usually crave"
          })
      },
      config: {
        provider: "openai-compatible",
        model: "structured-memory-model",
        enabled: true,
        baseUrl: "https://api.example.com/v1",
        apiKey: "test-key"
      }
    });

    const plan = await planner.plan({
      query: "我平时馋什么？",
      nodes: [existingHotpotPreference],
      summaries: []
    });

    expect(plan).toEqual({
      queryTerms: ["火锅", "吃的偏好"],
      targetTypes: ["preference"],
      rankedNodeIds: ["memory_hotpot"]
    });
  });
});
