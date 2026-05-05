import { afterEach, describe, expect, it } from "vitest";
import { MemoryService } from "../../../../src/core/memory/memoryService";
import { MemoryRepository } from "../../../../src/core/storage/repositories/memoryRepository";
import { cleanupTestServices, createTestServices } from "../../../helpers/testServices";

afterEach(cleanupTestServices);

describe("MemoryService", () => {
  it("uses injected planners to merge duplicate semantic memories and persist model-planned edges", async () => {
    const services = createTestServices("aurabot-memory-service-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const session = services.sessionRepository.getOrCreateMainSession(profile.id);
    const repository = new MemoryRepository(services.database);
    const service = new MemoryService(repository, {
      mergePlanner: {
        plan: async ({ candidate, existingNodes }) => {
          const existingPreference = existingNodes.find(
            (node) => node.type === "preference" && node.content.includes("火锅")
          );

          if (candidate.type === "preference" && existingPreference) {
            return {
              action: "update_existing",
              targetNodeId: existingPreference.id,
              mergedContent: "用户喜欢火锅",
              importance: 0.82,
              reason: "same semantic preference"
            };
          }

          return { action: "create" };
        }
      },
      relationPlanner: {
        plan: async ({ newNode, existingNodes }) =>
          newNode.type === "event"
            ? existingNodes
                .filter((node) => node.type === "preference" && node.content.includes("火锅"))
                .map((node) => ({
                  targetNodeId: node.id,
                  relationType: "preference_for" as const,
                  weight: 0.91
                }))
            : []
      }
    });

    await service.enqueueWrite({
      profileId: profile.id,
      sourceSessionId: session.id,
      node: {
        type: "preference",
        content: "用户喜欢火锅",
        importance: 0.72
      },
      edges: []
    });
    await service.enqueueWrite({
      profileId: profile.id,
      sourceSessionId: session.id,
      node: {
        type: "preference",
        content: "用户挺爱吃火锅",
        importance: 0.76
      },
      edges: []
    });
    await service.enqueueWrite({
      profileId: profile.id,
      sourceSessionId: session.id,
      node: {
        type: "event",
        content: "用户明天要和阿杰去吃火锅",
        importance: 0.84
      },
      edges: []
    });

    const context = service.retrieveForProfile(profile.id);
    const preferences = context.nodes.filter((node) => node.type === "preference");
    const event = context.nodes.find((node) => node.type === "event");

    expect(preferences).toHaveLength(1);
    expect(preferences[0]?.content).toBe("用户喜欢火锅");
    expect(preferences[0]?.importance).toBe(0.82);
    expect(context.edges).toContainEqual(
      expect.objectContaining({
        sourceNodeId: event?.id,
        targetNodeId: preferences[0]?.id,
        relationType: "preference_for",
        weight: 0.91
      })
    );
  });

  it("uses recall planner semantic terms to retrieve memories beyond keyword matching", async () => {
    const services = createTestServices("aurabot-memory-service-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const session = services.sessionRepository.getOrCreateMainSession(profile.id);
    const repository = new MemoryRepository(services.database);
    const service = new MemoryService(repository, {
      recallPlanner: {
        plan: async () => ({
          queryTerms: ["火锅"],
          targetTypes: ["preference"],
          rankedNodeIds: []
        })
      }
    });

    await service.enqueueWrite({
      profileId: profile.id,
      sourceSessionId: session.id,
      node: {
        type: "preference",
        content: "用户喜欢火锅",
        importance: 0.72
      },
      edges: []
    });

    const recall = await service.retrieveRelevant(profile.id, "我平时馋什么？");

    expect(recall.nodes).toContainEqual(
      expect.objectContaining({
        type: "preference",
        content: "用户喜欢火锅"
      })
    );
  });

  it("keeps a debug trace for the most recent recall seeds and graph expansion", async () => {
    const services = createTestServices("aurabot-memory-service-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const session = services.sessionRepository.getOrCreateMainSession(profile.id);
    const repository = new MemoryRepository(services.database);
    const service = new MemoryService(repository, {
      recallPlanner: {
        plan: async ({ nodes }) => ({
          queryTerms: ["阿杰"],
          targetTypes: ["event"],
          rankedNodeIds: [nodes.find((node) => node.type === "event")?.id ?? "missing-node"]
        })
      }
    });

    const preferenceId = await service.enqueueWrite({
      profileId: profile.id,
      sourceSessionId: session.id,
      node: {
        type: "preference",
        content: "用户喜欢火锅",
        importance: 0.72
      },
      edges: []
    });
    const eventId = await service.enqueueWrite({
      profileId: profile.id,
      sourceSessionId: session.id,
      node: {
        type: "event",
        content: "用户明天要和阿杰去吃火锅",
        importance: 0.84
      },
      edges: [
        {
          targetNodeId: preferenceId,
          relationType: "preference_for",
          weight: 0.91
        }
      ]
    });

    await service.retrieveRelevant(profile.id, "阿杰那件事我该注意什么？");

    expect(service.getLastRecallTrace()).toMatchObject({
      query: "阿杰那件事我该注意什么？",
      recallPlan: {
        queryTerms: ["阿杰"],
        targetTypes: ["event"],
        rankedNodeIds: [eventId]
      },
      seedNodeIds: [eventId],
      expandedNodeIds: [preferenceId],
      returnedNodeIds: expect.arrayContaining([eventId, preferenceId]),
      traversedEdges: [
        {
          sourceNodeId: eventId,
          targetNodeId: preferenceId,
          relationType: "preference_for",
          weight: 0.91,
          depth: 1
        }
      ]
    });
  });
});
