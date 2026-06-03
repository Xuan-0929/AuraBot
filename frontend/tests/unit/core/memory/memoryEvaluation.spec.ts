import { afterEach, describe, expect, it } from "vitest";
import { MemoryService } from "@core/memory/memoryService";
import {
  runMemoryEvaluationCase,
  type MemoryEvaluationCase
} from "@core/memory/memoryEvaluation";
import { MemoryRepository } from "@core/storage/repositories/memoryRepository";
import fixture from "../../../fixtures/memory/recall-evaluation.json";
import { cleanupTestServices, createTestServices } from "../../../helpers/testServices";

afterEach(cleanupTestServices);

describe("memory evaluation fixtures", () => {
  it("runs fixed recall cases against MemoryService and reports missing expectations", async () => {
    const services = createTestServices("aurabot-memory-eval-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const session = services.sessionRepository.getOrCreateMainSession(profile.id);
    const repository = new MemoryRepository(services.database);
    const service = new MemoryService(repository);
    const cases = fixture.cases as MemoryEvaluationCase[];
    const results = [];

    for (const evaluationCase of cases) {
      results.push(
        await runMemoryEvaluationCase({
          evaluationCase,
          service,
          profileId: profile.id,
          sourceSessionId: session.id
        })
      );
    }

    expect(results).toHaveLength(3);
    expect(results.every((result) => result.passed)).toBe(true);
    expect(results.map((result) => result.caseId)).toEqual([
      "preference-direct-keyword",
      "event-to-preference-graph-hop",
      "follow-up-due-filter"
    ]);
    expect(results[1]).toMatchObject({
      recalledLabels: expect.arrayContaining(["ajie-hotpot-event", "hotpot-preference"]),
      seedLabels: ["ajie-hotpot-event"],
      expandedLabels: expect.arrayContaining(["hotpot-preference"]),
      traversedEdges: [
        expect.objectContaining({
          sourceLabel: "ajie-hotpot-event",
          targetLabel: "hotpot-preference",
          relationType: "preference_for"
        })
      ],
      missingExpectedLabels: []
    });
  });
});
