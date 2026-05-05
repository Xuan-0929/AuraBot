import type { MemoryEdge, MemoryNode, MemoryWriteRequest } from "../../shared/schemas/memory";
import type { MemoryService } from "./memoryService";

export interface MemoryEvaluationEdge {
  targetLabel: string;
  relationType: MemoryEdge["relationType"];
  weight: number;
}

export interface MemoryEvaluationMemory {
  label: string;
  type: MemoryNode["type"];
  content: string;
  importance: number;
  startsAt?: string;
  endsAt?: string;
  edges?: MemoryEvaluationEdge[];
}

export interface MemoryEvaluationExpectation {
  recalledLabels: string[];
  seedLabels?: string[];
  expandedLabels?: string[];
  excludedLabels?: string[];
}

export interface MemoryEvaluationCase {
  id: string;
  description: string;
  memories: MemoryEvaluationMemory[];
  query: string;
  expected: MemoryEvaluationExpectation;
}

export interface MemoryEvaluationCaseResult {
  caseId: string;
  passed: boolean;
  recalledLabels: string[];
  seedLabels: string[];
  expandedLabels: string[];
  returnedLabels: string[];
  traversedEdges: Array<{
    sourceLabel: string;
    targetLabel: string;
    relationType?: string;
    weight?: number;
    depth: number;
  }>;
  missingExpectedLabels: string[];
  unexpectedLabels: string[];
}

export async function runMemoryEvaluationCase(input: {
  evaluationCase: MemoryEvaluationCase;
  service: MemoryService;
  profileId: string;
  sourceSessionId: string;
}): Promise<MemoryEvaluationCaseResult> {
  const labelToId = new Map<string, string>();

  for (const memory of input.evaluationCase.memories) {
    const nodeId = await input.service.enqueueWrite({
      profileId: input.profileId,
      sourceSessionId: input.sourceSessionId,
      node: {
        type: memory.type,
        content: memory.content,
        importance: memory.importance,
        startsAt: memory.startsAt,
        endsAt: memory.endsAt
      },
      edges: resolveEvaluationEdges(memory.edges ?? [], labelToId)
    });

    labelToId.set(memory.label, nodeId);
  }

  const idToLabel = new Map([...labelToId.entries()].map(([label, id]) => [id, label]));
  const recall = await input.service.retrieveRelevant(input.profileId, input.evaluationCase.query);
  const trace = input.service.getLastRecallTrace();
  const recalledLabels = recall.nodes.map((node) => idToLabel.get(node.id)).filter(isDefined);
  const seedLabels = (trace?.seedNodeIds ?? []).map((nodeId) => idToLabel.get(nodeId)).filter(isDefined);
  const expandedLabels = (trace?.expandedNodeIds ?? []).map((nodeId) => idToLabel.get(nodeId)).filter(isDefined);
  const returnedLabels = (trace?.returnedNodeIds ?? []).map((nodeId) => idToLabel.get(nodeId)).filter(isDefined);
  const traversedEdges: MemoryEvaluationCaseResult["traversedEdges"] = [];

  for (const edge of trace?.traversedEdges ?? []) {
    const sourceLabel = idToLabel.get(edge.sourceNodeId);
    const targetLabel = idToLabel.get(edge.targetNodeId);

    if (!sourceLabel || !targetLabel) {
      continue;
    }

    traversedEdges.push({
      sourceLabel,
      targetLabel,
      relationType: edge.relationType,
      weight: edge.weight,
      depth: edge.depth
    });
  }
  const missingExpectedLabels = input.evaluationCase.expected.recalledLabels.filter(
    (label) => !recalledLabels.includes(label)
  );
  const unexpectedLabels = (input.evaluationCase.expected.excludedLabels ?? []).filter((label) =>
    recalledLabels.includes(label)
  );
  const missingSeeds = (input.evaluationCase.expected.seedLabels ?? []).filter((label) => !seedLabels.includes(label));
  const missingExpanded = (input.evaluationCase.expected.expandedLabels ?? []).filter(
    (label) => !expandedLabels.includes(label)
  );

  return {
    caseId: input.evaluationCase.id,
    passed:
      missingExpectedLabels.length === 0 &&
      unexpectedLabels.length === 0 &&
      missingSeeds.length === 0 &&
      missingExpanded.length === 0,
    recalledLabels,
    seedLabels,
    expandedLabels,
    returnedLabels,
    traversedEdges,
    missingExpectedLabels: [...missingExpectedLabels, ...missingSeeds, ...missingExpanded],
    unexpectedLabels
  };
}

function resolveEvaluationEdges(
  edges: MemoryEvaluationEdge[],
  labelToId: Map<string, string>
): MemoryWriteRequest["edges"] {
  return edges.map((edge) => {
    const targetNodeId = labelToId.get(edge.targetLabel);

    if (!targetNodeId) {
      throw new Error(`Unknown memory evaluation target label: ${edge.targetLabel}`);
    }

    return {
      targetNodeId,
      relationType: edge.relationType,
      weight: edge.weight
    };
  });
}

function isDefined<T>(value: T | undefined): value is T {
  return value !== undefined;
}
