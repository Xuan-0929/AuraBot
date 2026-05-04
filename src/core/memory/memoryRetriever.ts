import {
  DEFAULT_MEMORY_TRAVERSAL_DEPTH,
  DEFAULT_MEMORY_TRAVERSAL_LIMIT
} from "../../shared/constants/memory";
import type { MemoryEdge, MemoryNode, MemorySummary } from "../../shared/schemas/memory";
import { collectRelatedMemoryNodeIds } from "./graphTraversal";

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
}

export function retrieveMemoryContext(input: RetrieveMemoryContextInput): RetrievedMemoryContext {
  const relatedIds = collectRelatedMemoryNodeIds({
    startNodeIds: input.seedNodeIds,
    edges: input.edges,
    maxDepth: input.maxDepth ?? DEFAULT_MEMORY_TRAVERSAL_DEPTH,
    maxNodes: input.maxNodes ?? DEFAULT_MEMORY_TRAVERSAL_LIMIT
  });
  const allowedIds = new Set([...input.seedNodeIds, ...relatedIds]);

  return {
    summaries: input.summaries,
    nodes: input.nodes.filter((node) => allowedIds.has(node.id))
  };
}
