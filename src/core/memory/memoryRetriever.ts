import {
  DEFAULT_MEMORY_TRAVERSAL_DEPTH,
  DEFAULT_MEMORY_TRAVERSAL_LIMIT
} from "../../shared/constants/memory";
import type { MemoryEdge, MemoryNode, MemorySummary } from "../../shared/schemas/memory";
import { collectRelatedMemoryTrace, type TraversedMemoryEdge } from "./graphTraversal";

export interface RetrieveMemoryContextInput {
  seedNodeIds: string[];
  summaries: MemorySummary[];
  nodes: MemoryNode[];
  edges: MemoryEdge[];
  maxDepth?: number;
  maxNodes?: number;
}

export interface RetrievedMemoryContext {
  summaries: MemorySummary[];
  nodes: MemoryNode[];
  trace: RetrievedMemoryTrace;
}

export interface RetrievedMemoryTrace {
  seedNodeIds: string[];
  expandedNodeIds: string[];
  returnedNodeIds: string[];
  traversedEdges: TraversedMemoryEdge[];
}

export function retrieveMemoryContext(input: RetrieveMemoryContextInput): RetrievedMemoryContext {
  const relatedTrace = collectRelatedMemoryTrace({
    startNodeIds: input.seedNodeIds,
    edges: input.edges,
    maxDepth: input.maxDepth ?? DEFAULT_MEMORY_TRAVERSAL_DEPTH,
    maxNodes: input.maxNodes ?? DEFAULT_MEMORY_TRAVERSAL_LIMIT
  });
  const allowedIds = new Set([...input.seedNodeIds, ...relatedTrace.nodeIds]);
  const nodes = input.nodes.filter((node) => allowedIds.has(node.id));

  return {
    summaries: input.summaries,
    nodes,
    trace: {
      seedNodeIds: input.seedNodeIds,
      expandedNodeIds: relatedTrace.nodeIds,
      returnedNodeIds: nodes.map((node) => node.id),
      traversedEdges: relatedTrace.traversedEdges
    }
  };
}
