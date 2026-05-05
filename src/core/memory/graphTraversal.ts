export interface MemoryRelationEdge {
  sourceNodeId: string;
  targetNodeId: string;
  relationType?: string;
  weight?: number;
}

export interface CollectRelatedMemoryNodeIdsInput {
  startNodeIds: string[];
  edges: MemoryRelationEdge[];
  maxDepth: number;
  maxNodes: number;
  direction?: "outgoing" | "incoming" | "both";
}

interface Candidate {
  nodeId: string;
  depth: number;
  score: number;
  viaEdge?: MemoryRelationEdge;
}

export function collectRelatedMemoryNodeIds(input: CollectRelatedMemoryNodeIdsInput): string[] {
  return collectRelatedMemoryTrace(input).nodeIds;
}

export interface TraversedMemoryEdge {
  sourceNodeId: string;
  targetNodeId: string;
  relationType?: string;
  weight?: number;
  depth: number;
}

export interface RelatedMemoryTrace {
  nodeIds: string[];
  traversedEdges: TraversedMemoryEdge[];
}

export function collectRelatedMemoryTrace(input: CollectRelatedMemoryNodeIdsInput): RelatedMemoryTrace {
  const direction = input.direction ?? "both";
  const visited = new Set(input.startNodeIds);
  const nodeIds: string[] = [];
  const traversedEdges: TraversedMemoryEdge[] = [];
  const frontier: Candidate[] = [];

  for (const nodeId of input.startNodeIds) {
    pushNeighbors(frontier, input.edges, nodeId, 0, 1, direction);
  }

  while (frontier.length > 0 && nodeIds.length < input.maxNodes) {
    frontier.sort((left, right) => right.score - left.score || left.depth - right.depth);
    const next = frontier.shift();

    if (!next || visited.has(next.nodeId) || next.depth > input.maxDepth) {
      continue;
    }

    visited.add(next.nodeId);
    nodeIds.push(next.nodeId);

    if (next.viaEdge) {
      traversedEdges.push({
        sourceNodeId: next.viaEdge.sourceNodeId,
        targetNodeId: next.viaEdge.targetNodeId,
        relationType: next.viaEdge.relationType,
        weight: next.viaEdge.weight,
        depth: next.depth
      });
    }

    if (next.depth < input.maxDepth) {
      pushNeighbors(frontier, input.edges, next.nodeId, next.depth, next.score, direction);
    }
  }

  return {
    nodeIds,
    traversedEdges
  };
}

function pushNeighbors(
  frontier: Candidate[],
  edges: MemoryRelationEdge[],
  nodeId: string,
  depth: number,
  parentScore: number,
  direction: "outgoing" | "incoming" | "both"
): void {
  for (const edge of edges) {
    const weight = edge.weight ?? 0.5;

    if ((direction === "outgoing" || direction === "both") && edge.sourceNodeId === nodeId) {
      frontier.push({ nodeId: edge.targetNodeId, depth: depth + 1, score: parentScore * weight, viaEdge: edge });
    }

    if ((direction === "incoming" || direction === "both") && edge.targetNodeId === nodeId) {
      frontier.push({ nodeId: edge.sourceNodeId, depth: depth + 1, score: parentScore * weight, viaEdge: edge });
    }
  }
}
