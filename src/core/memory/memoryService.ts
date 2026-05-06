import type { MemoryWriteRequest } from "../../shared/schemas/memory";
import type { MemoryNode } from "../../shared/schemas/memory";
import type { MemoryRepository } from "../storage/repositories/memoryRepository";
import { deriveMemoryConcepts } from "./memoryAssociations";
import { retrieveMemoryContext, type RetrievedMemoryContext, type RetrievedMemoryTrace } from "./memoryRetriever";
import { MemoryWriteQueue } from "./memoryWriter";
import {
  DeterministicMemoryMergePlanner,
  DeterministicMemoryRecallPlanner,
  DeterministicMemoryRelationPlanner,
  type MemoryMergePlanner,
  type MemoryRecallPlan,
  type MemoryRecallPlanner,
  type MemoryRelationPlanner
} from "./modelBackedMemoryModel";

export interface MemoryServiceOptions {
  mergePlanner?: MemoryMergePlanner;
  relationPlanner?: MemoryRelationPlanner;
  recallPlanner?: MemoryRecallPlanner;
}

export interface MemoryRecallDebugTrace extends RetrievedMemoryTrace {
  query: string;
  recallPlan: MemoryRecallPlan;
}

export class MemoryService {
  private readonly writeQueue = new MemoryWriteQueue();
  private readonly mergePlanner: MemoryMergePlanner;
  private readonly relationPlanner: MemoryRelationPlanner;
  private readonly recallPlanner: MemoryRecallPlanner;
  private lastRecallTrace: MemoryRecallDebugTrace | undefined;

  constructor(
    private readonly memoryRepository: MemoryRepository,
    options: MemoryServiceOptions = {}
  ) {
    this.mergePlanner = options.mergePlanner ?? new DeterministicMemoryMergePlanner();
    this.relationPlanner = options.relationPlanner ?? new DeterministicMemoryRelationPlanner();
    this.recallPlanner = options.recallPlanner ?? new DeterministicMemoryRecallPlanner();
  }

  getRepository(): MemoryRepository {
    return this.memoryRepository;
  }

  enqueueWrite(request: MemoryWriteRequest): Promise<string> {
    return this.writeQueue.enqueue(async () => {
      const context = this.memoryRepository.getMemoryContext(request.profileId);
      const mergeDecision = await this.mergePlanner.plan({
        candidate: request.node,
        existingNodes: context.nodes
      });

      if (mergeDecision.action === "update_existing") {
        return this.memoryRepository.updateNodeFromWriteRequest({
          profileId: request.profileId,
          nodeId: mergeDecision.targetNodeId,
          node: {
            ...request.node,
            content: mergeDecision.mergedContent,
            importance: mergeDecision.importance
          }
        });
      }

      const associativeEdges = await this.relationPlanner.plan({
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

  retrieveDueFollowUps(profileId: string, nowIso: string, lookaheadMs = 0): MemoryNode[] {
    const cutoff = new Date(new Date(nowIso).getTime() + lookaheadMs);
    const context = this.memoryRepository.getMemoryContext(profileId);

    return context.nodes.filter((node) => {
      if (node.type !== "follow_up" || node.state !== "active" || !node.startsAt) {
        return false;
      }

      const startsAt = new Date(node.startsAt);

      return Number.isFinite(startsAt.getTime()) && startsAt.getTime() <= cutoff.getTime();
    });
  }

  completeFollowUps(profileId: string, nodeIds: string[]): void {
    for (const nodeId of dedupeIds(nodeIds)) {
      this.memoryRepository.updateNodeState({
        profileId,
        nodeId,
        state: "completed"
      });
    }
  }

  getLastRecallTrace(): MemoryRecallDebugTrace | undefined {
    return this.lastRecallTrace;
  }

  async retrieveRelevant(profileId: string, query: string): Promise<RetrievedMemoryContext> {
    const context = this.memoryRepository.getMemoryContext(profileId);
    const recallPlan = await this.recallPlanner.plan({
      query,
      nodes: context.nodes,
      summaries: context.summaries
    });
    const queryForScoring = buildRecallScoringQuery(query, recallPlan);
    const rankedNodeIds = recallPlan.rankedNodeIds.filter((nodeId) =>
      context.nodes.some((node) => node.id === nodeId)
    );
    const scoredNodeIds = context.nodes
      .map((node) => ({ node, score: scoreMemoryNode(node, queryForScoring, recallPlan) }))
      .filter((item) => item.score > 0)
      .sort((left, right) => right.score - left.score)
      .slice(0, 4)
      .map((item) => item.node.id);
    const seedNodeIds = dedupeIds([...rankedNodeIds, ...scoredNodeIds]).slice(0, 4);

    if (seedNodeIds.length === 0) {
      this.lastRecallTrace = {
        query,
        recallPlan,
        seedNodeIds: [],
        expandedNodeIds: [],
        returnedNodeIds: [],
        traversedEdges: []
      };

      return {
        summaries: context.summaries,
        nodes: [],
        trace: this.lastRecallTrace
      };
    }

    const retrievedContext = retrieveMemoryContext({
      seedNodeIds,
      summaries: context.summaries,
      nodes: context.nodes,
      edges: context.edges,
      maxDepth: 2,
      maxNodes: 8
    });

    this.lastRecallTrace = {
      query,
      recallPlan,
      ...retrievedContext.trace
    };

    return retrievedContext;
  }
}

function scoreMemoryNode(node: MemoryNode, query: string, recallPlan?: MemoryRecallPlan): number {
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

  if (recallPlan?.targetTypes.includes(node.type)) {
    score += 1;
  }

  return score * (1 + node.importance);
}

function buildRecallScoringQuery(query: string, recallPlan: MemoryRecallPlan): string {
  return [query, ...recallPlan.queryTerms].join(" ");
}

function dedupeIds(ids: string[]): string[] {
  return [...new Set(ids)];
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
