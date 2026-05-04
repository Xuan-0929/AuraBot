import type { MemoryWriteRequest } from "../../shared/schemas/memory";
import type { MemoryNode } from "../../shared/schemas/memory";
import type { MemoryRepository } from "../storage/repositories/memoryRepository";
import { buildAssociativeMemoryEdges, deriveMemoryConcepts } from "./memoryAssociations";
import { retrieveMemoryContext, type RetrievedMemoryContext } from "./memoryRetriever";
import { MemoryWriteQueue } from "./memoryWriter";

export class MemoryService {
  private readonly writeQueue = new MemoryWriteQueue();

  constructor(private readonly memoryRepository: MemoryRepository) {}

  enqueueWrite(request: MemoryWriteRequest): Promise<string> {
    return this.writeQueue.enqueue(() => {
      const context = this.memoryRepository.getMemoryContext(request.profileId);
      const associativeEdges = buildAssociativeMemoryEdges({
        newNode: request.node,
        existingNodes: context.nodes
      });

      return this.memoryRepository.createNodeFromWriteRequest({
        ...request,
        edges: mergeMemoryEdges(request.edges, associativeEdges)
      });
    });
  }

  retrieveForProfile(profileId: string) {
    return this.memoryRepository.getMemoryContext(profileId);
  }

  retrieveRelevant(profileId: string, query: string): RetrievedMemoryContext {
    const context = this.memoryRepository.getMemoryContext(profileId);
    const seedNodeIds = context.nodes
      .map((node) => ({ node, score: scoreMemoryNode(node, query) }))
      .filter((item) => item.score > 0)
      .sort((left, right) => right.score - left.score)
      .slice(0, 4)
      .map((item) => item.node.id);

    if (seedNodeIds.length === 0) {
      return {
        summaries: context.summaries,
        nodes: []
      };
    }

    return retrieveMemoryContext({
      seedNodeIds,
      summaries: context.summaries,
      nodes: context.nodes,
      edges: context.edges,
      maxDepth: 2,
      maxNodes: 8
    });
  }
}

function scoreMemoryNode(node: MemoryNode, query: string): number {
  let score = 0;
  const queryTokens = extractRecallTokens(query);
  const nodeConcepts = deriveMemoryConcepts(node.content);

  for (const token of queryTokens) {
    if (node.content.includes(token)) {
      score += token.length >= 2 ? 2 : 1;
    }

    if (nodeConcepts.has(token)) {
      score += 2;
    }
  }

  if (node.type === "preference" && /(喜欢|讨厌|偏好|吃什么|玩什么|看什么)/.test(query)) {
    score += 3;
  }

  if (node.type === "event" && /(下周|明天|后天|今晚|哪天|有什么事|安排|提醒|别忘)/.test(query)) {
    score += 4;
  }

  if (node.type === "follow_up" && /(提醒|别忘|记得|跟进)/.test(query)) {
    score += 3;
  }

  return score * (1 + node.importance);
}

function extractRecallTokens(query: string): string[] {
  const tokens = query
    .replace(/[，。,.！？!?；;：:\n]/g, " ")
    .split(/\s+/)
    .flatMap((chunk) => chunk.split(/(喜欢|讨厌|火锅|跑步|演唱会|考试|面试|下周|明天|后天|今晚|提醒)/))
    .map((token) => token.trim())
    .filter((token) => token.length >= 2 && !/^(我|你|什么|来着|有|的)$/.test(token));

  return [...new Set([...tokens, ...deriveMemoryConcepts(query)])];
}

function mergeMemoryEdges(primary: MemoryWriteRequest["edges"], secondary: MemoryWriteRequest["edges"]) {
  const seen = new Set<string>();
  const result: MemoryWriteRequest["edges"] = [];

  for (const edge of [...primary, ...secondary]) {
    const key = `${edge.targetNodeId}:${edge.relationType}`;
    if (seen.has(key)) {
      continue;
    }

    seen.add(key);
    result.push(edge);
  }

  return result;
}
