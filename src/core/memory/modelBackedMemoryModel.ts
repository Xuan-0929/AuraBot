import { z } from "zod";
import type { ModelRoleConfig } from "../../shared/schemas/config";
import {
  isoDateStringSchema,
  memoryEdgeTypeSchema,
  memoryNodeTypeSchema,
  type MemoryNode,
  type MemorySummary,
  type MemoryWriteRequest
} from "../../shared/schemas/memory";
import type { ChatCompletionRequest } from "../llm/openAiCompatibleClient";
import { OpenAICompatibleChatCompletionClient } from "../llm/openAiCompatibleClient";
import {
  createMissingModelConfigurationError,
  createUnsupportedModelProviderError,
  isUsableModelRoleConfig,
  ModelInvocationError,
  shouldUseDeterministicModelTestFallback
} from "../llm/modelErrors";
import { buildAssociativeMemoryEdges } from "./memoryAssociations";
import { DeterministicMemoryExtractor, type ExtractedMemoryCandidate, type MemoryExtractionInput, type MemoryExtractor } from "./memoryExtractor";
import { LangChainStructuredMemoryClient } from "./langChainMemoryModel";

export interface MemoryModelClient {
  complete(input: ChatCompletionRequest): Promise<string>;
}

export interface ModelBackedMemoryOptions {
  client: MemoryModelClient;
  config?: ModelRoleConfig;
}

export interface MemoryModelFactoryDependencies {
  openAICompatibleClient?: MemoryModelClient;
  langChainExtractionClient?: MemoryModelClient;
  langChainMergeClient?: MemoryModelClient;
  langChainRelationClient?: MemoryModelClient;
  langChainRecallClient?: MemoryModelClient;
}

export type MemoryMergeDecision =
  | { action: "create" }
  | {
      action: "update_existing";
      targetNodeId: string;
      mergedContent: string;
      importance: number;
      reason: string;
    };

export interface MemoryMergePlannerInput {
  candidate: MemoryWriteRequest["node"];
  existingNodes: MemoryNode[];
}

export interface MemoryMergePlanner {
  plan(input: MemoryMergePlannerInput): Promise<MemoryMergeDecision>;
}

export interface MemoryRelationPlannerInput {
  newNode: MemoryWriteRequest["node"];
  existingNodes: MemoryNode[];
}

export interface MemoryRelationPlanner {
  plan(input: MemoryRelationPlannerInput): Promise<MemoryWriteRequest["edges"]>;
}

export interface MemoryRecallPlannerInput {
  query: string;
  nodes: MemoryNode[];
  summaries: MemorySummary[];
}

export interface MemoryRecallPlan {
  queryTerms: string[];
  targetTypes: MemoryNode["type"][];
  rankedNodeIds: string[];
}

export interface MemoryRecallPlanner {
  plan(input: MemoryRecallPlannerInput): Promise<MemoryRecallPlan>;
}

class UnavailableMemoryExtractor implements MemoryExtractor {
  constructor(private readonly error: Error) {}

  async extract(): Promise<ExtractedMemoryCandidate[]> {
    throw this.error;
  }
}

class UnavailableMemoryMergePlanner implements MemoryMergePlanner {
  constructor(private readonly error: Error) {}

  async plan(): Promise<MemoryMergeDecision> {
    throw this.error;
  }
}

class UnavailableMemoryRelationPlanner implements MemoryRelationPlanner {
  constructor(private readonly error: Error) {}

  async plan(): Promise<MemoryWriteRequest["edges"]> {
    throw this.error;
  }
}

class UnavailableMemoryRecallPlanner implements MemoryRecallPlanner {
  constructor(private readonly error: Error) {}

  async plan(): Promise<MemoryRecallPlan> {
    throw this.error;
  }
}

const extractedCandidateSchema = z
  .object({
    type: memoryNodeTypeSchema,
    content: z.string().min(1),
    topic: z.string().min(1),
    importance: z.number().min(0).max(1),
    startsAt: isoDateStringSchema.nullish(),
    endsAt: isoDateStringSchema.nullish(),
    confidence: z.number().min(0).max(1).optional(),
    relationToPrevious: z
      .object({
        relationType: memoryEdgeTypeSchema,
        weight: z.number().min(0).max(1)
      })
      .optional()
  })
  .passthrough();

const extractionSchema = z.object({
  candidates: z.array(extractedCandidateSchema).default([])
});

const mergeDecisionSchema = z.discriminatedUnion("action", [
  z.object({
    action: z.literal("create")
  }),
  z.object({
    action: z.literal("update_existing"),
    targetNodeId: z.string().min(1),
    mergedContent: z.string().min(1),
    importance: z.number().min(0).max(1),
    reason: z.string().min(1)
  })
]);

const relationPlanSchema = z.object({
  edges: z
    .array(
      z.object({
        targetNodeId: z.string().min(1),
        relationType: memoryEdgeTypeSchema,
        weight: z.number().min(0).max(1)
      })
    )
    .default([])
});

const recallPlanSchema = z.object({
  queryTerms: z.array(z.string().min(1)).default([]),
  targetTypes: z.array(memoryNodeTypeSchema).default([]),
  rankedNodeIds: z.array(z.string().min(1)).default([])
});

export class ModelBackedMemoryExtractor implements MemoryExtractor {
  constructor(private readonly options: ModelBackedMemoryOptions) {}

  async extract(input: MemoryExtractionInput): Promise<ExtractedMemoryCandidate[]> {
    try {
      const content = await this.options.client.complete({
        baseUrl: this.options.config?.baseUrl ?? "",
        apiKey: this.options.config?.apiKey ?? "",
        model: this.options.config?.model ?? "memory-model",
        temperature: this.options.config?.temperature ?? 0,
        timeoutMs: this.options.config?.timeoutMs,
        messages: buildExtractionPrompt(input)
      });
      const parsed = extractionSchema.parse(parseJsonFromModel(content));

      return parsed.candidates.map((candidate) => ({
        topic: candidate.topic,
        node: {
          type: candidate.type,
          content: candidate.content,
          importance: candidate.importance,
          startsAt: candidate.startsAt ?? undefined,
          endsAt: candidate.endsAt ?? undefined
        },
        relationToPrevious: candidate.relationToPrevious
          ? {
              targetNodeId: "",
              relationType: candidate.relationToPrevious.relationType,
              weight: candidate.relationToPrevious.weight
            }
          : undefined
      }));
    } catch (error) {
      throw new ModelInvocationError("memoryModel", error);
    }
  }
}

export class DeterministicMemoryMergePlanner implements MemoryMergePlanner {
  async plan(input: MemoryMergePlannerInput): Promise<MemoryMergeDecision> {
    const existing = input.existingNodes.find(
      (node) => node.type === input.candidate.type && node.content === input.candidate.content
    );

    if (!existing) {
      return { action: "create" };
    }

    return {
      action: "update_existing",
      targetNodeId: existing.id,
      mergedContent: existing.content,
      importance: Math.max(existing.importance, input.candidate.importance),
      reason: "deterministic exact memory match"
    };
  }
}

export class ModelBackedMemoryMergePlanner implements MemoryMergePlanner {
  constructor(private readonly options: ModelBackedMemoryOptions) {}

  async plan(input: MemoryMergePlannerInput): Promise<MemoryMergeDecision> {
    if (input.existingNodes.length === 0) {
      return { action: "create" };
    }

    try {
      const content = await this.options.client.complete({
        baseUrl: this.options.config?.baseUrl ?? "",
        apiKey: this.options.config?.apiKey ?? "",
        model: this.options.config?.model ?? "memory-model",
        temperature: this.options.config?.temperature ?? 0,
        timeoutMs: this.options.config?.timeoutMs,
        messages: buildMergePrompt(input)
      });
      const decision = mergeDecisionSchema.parse(parseJsonFromModel(content));

      if (decision.action === "create") {
        return decision;
      }

      if (!input.existingNodes.some((node) => node.id === decision.targetNodeId)) {
        throw new Error(`Memory merge model returned unknown target node id: ${decision.targetNodeId}`);
      }

      return decision;
    } catch (error) {
      throw new ModelInvocationError("memoryModel", error);
    }
  }
}

export class DeterministicMemoryRelationPlanner implements MemoryRelationPlanner {
  async plan(input: MemoryRelationPlannerInput): Promise<MemoryWriteRequest["edges"]> {
    return buildAssociativeMemoryEdges({
      newNode: input.newNode,
      existingNodes: input.existingNodes
    });
  }
}

export class ModelBackedMemoryRelationPlanner implements MemoryRelationPlanner {
  constructor(private readonly options: ModelBackedMemoryOptions) {}

  async plan(input: MemoryRelationPlannerInput): Promise<MemoryWriteRequest["edges"]> {
    if (input.existingNodes.length === 0) {
      return [];
    }

    try {
      const content = await this.options.client.complete({
        baseUrl: this.options.config?.baseUrl ?? "",
        apiKey: this.options.config?.apiKey ?? "",
        model: this.options.config?.model ?? "memory-model",
        temperature: this.options.config?.temperature ?? 0,
        timeoutMs: this.options.config?.timeoutMs,
        messages: buildRelationPrompt(input)
      });
      const parsed = relationPlanSchema.parse(parseJsonFromModel(content));
      const allowedTargetIds = new Set(input.existingNodes.map((node) => node.id));

      return parsed.edges
        .filter((edge) => allowedTargetIds.has(edge.targetNodeId))
        .map((edge) => ({
          targetNodeId: edge.targetNodeId,
          relationType: edge.relationType,
          weight: edge.weight
        }));
    } catch (error) {
      throw new ModelInvocationError("memoryModel", error);
    }
  }
}

export class DeterministicMemoryRecallPlanner implements MemoryRecallPlanner {
  async plan(_input: MemoryRecallPlannerInput): Promise<MemoryRecallPlan> {
    return {
      queryTerms: [],
      targetTypes: [],
      rankedNodeIds: []
    };
  }
}

export class ModelBackedMemoryRecallPlanner implements MemoryRecallPlanner {
  constructor(private readonly options: ModelBackedMemoryOptions) {}

  async plan(input: MemoryRecallPlannerInput): Promise<MemoryRecallPlan> {
    if (input.nodes.length === 0) {
      return {
        queryTerms: [],
        targetTypes: [],
        rankedNodeIds: []
      };
    }

    try {
      const content = await this.options.client.complete({
        baseUrl: this.options.config?.baseUrl ?? "",
        apiKey: this.options.config?.apiKey ?? "",
        model: this.options.config?.model ?? "memory-model",
        temperature: this.options.config?.temperature ?? 0,
        timeoutMs: this.options.config?.timeoutMs,
        messages: buildRecallPrompt(input)
      });
      const parsed = recallPlanSchema.parse(parseJsonFromModel(content));
      const allowedNodeIds = new Set(input.nodes.map((node) => node.id));

      return {
        queryTerms: dedupeStrings(parsed.queryTerms),
        targetTypes: [...new Set(parsed.targetTypes)],
        rankedNodeIds: parsed.rankedNodeIds.filter((nodeId) => allowedNodeIds.has(nodeId))
      };
    } catch (error) {
      throw new ModelInvocationError("memoryModel", error);
    }
  }
}

export function createMemoryExtractor(
  config: ModelRoleConfig,
  dependencies: MemoryModelFactoryDependencies = {}
): MemoryExtractor {
  if (!isUsableModelRoleConfig(config)) {
    if (shouldUseDeterministicModelTestFallback()) {
      return new DeterministicMemoryExtractor();
    }

    return new UnavailableMemoryExtractor(createMissingModelConfigurationError("memoryModel", config));
  }

  if (config.provider === "local-stub" && shouldUseDeterministicModelTestFallback()) {
    return new DeterministicMemoryExtractor();
  }

  if (config.provider === "langchain-openai") {
    return new ModelBackedMemoryExtractor({
      client: dependencies.langChainExtractionClient ?? new LangChainStructuredMemoryClient({ schema: extractionSchema }),
      config
    });
  }

  if (config.provider !== "openai-compatible") {
    return new UnavailableMemoryExtractor(createUnsupportedModelProviderError("memoryModel", config.provider));
  }

  return new ModelBackedMemoryExtractor({
    client: dependencies.openAICompatibleClient ?? new OpenAICompatibleChatCompletionClient(),
    config
  });
}

export function createMemoryMergePlanner(
  config: ModelRoleConfig,
  dependencies: MemoryModelFactoryDependencies = {}
): MemoryMergePlanner {
  if (!isUsableModelRoleConfig(config)) {
    if (shouldUseDeterministicModelTestFallback()) {
      return new DeterministicMemoryMergePlanner();
    }

    return new UnavailableMemoryMergePlanner(createMissingModelConfigurationError("memoryModel", config));
  }

  if (config.provider === "local-stub" && shouldUseDeterministicModelTestFallback()) {
    return new DeterministicMemoryMergePlanner();
  }

  if (config.provider === "langchain-openai") {
    return new ModelBackedMemoryMergePlanner({
      client: dependencies.langChainMergeClient ?? new LangChainStructuredMemoryClient({ schema: mergeDecisionSchema }),
      config
    });
  }

  if (config.provider !== "openai-compatible") {
    return new UnavailableMemoryMergePlanner(createUnsupportedModelProviderError("memoryModel", config.provider));
  }

  return new ModelBackedMemoryMergePlanner({
    client: dependencies.openAICompatibleClient ?? new OpenAICompatibleChatCompletionClient(),
    config
  });
}

export function createMemoryRelationPlanner(
  config: ModelRoleConfig,
  dependencies: MemoryModelFactoryDependencies = {}
): MemoryRelationPlanner {
  if (!isUsableModelRoleConfig(config)) {
    if (shouldUseDeterministicModelTestFallback()) {
      return new DeterministicMemoryRelationPlanner();
    }

    return new UnavailableMemoryRelationPlanner(createMissingModelConfigurationError("memoryModel", config));
  }

  if (config.provider === "local-stub" && shouldUseDeterministicModelTestFallback()) {
    return new DeterministicMemoryRelationPlanner();
  }

  if (config.provider === "langchain-openai") {
    return new ModelBackedMemoryRelationPlanner({
      client: dependencies.langChainRelationClient ?? new LangChainStructuredMemoryClient({ schema: relationPlanSchema }),
      config
    });
  }

  if (config.provider !== "openai-compatible") {
    return new UnavailableMemoryRelationPlanner(createUnsupportedModelProviderError("memoryModel", config.provider));
  }

  return new ModelBackedMemoryRelationPlanner({
    client: dependencies.openAICompatibleClient ?? new OpenAICompatibleChatCompletionClient(),
    config
  });
}

export function createMemoryRecallPlanner(
  config: ModelRoleConfig,
  dependencies: MemoryModelFactoryDependencies = {}
): MemoryRecallPlanner {
  if (!isUsableModelRoleConfig(config)) {
    if (shouldUseDeterministicModelTestFallback()) {
      return new DeterministicMemoryRecallPlanner();
    }

    return new UnavailableMemoryRecallPlanner(createMissingModelConfigurationError("memoryModel", config));
  }

  if (config.provider === "local-stub" && shouldUseDeterministicModelTestFallback()) {
    return new DeterministicMemoryRecallPlanner();
  }

  if (config.provider === "langchain-openai") {
    return new ModelBackedMemoryRecallPlanner({
      client: dependencies.langChainRecallClient ?? new LangChainStructuredMemoryClient({ schema: recallPlanSchema }),
      config
    });
  }

  if (config.provider !== "openai-compatible") {
    return new UnavailableMemoryRecallPlanner(createUnsupportedModelProviderError("memoryModel", config.provider));
  }

  return new ModelBackedMemoryRecallPlanner({
    client: dependencies.openAICompatibleClient ?? new OpenAICompatibleChatCompletionClient(),
    config
  });
}

function buildExtractionPrompt(input: MemoryExtractionInput): ChatCompletionRequest["messages"] {
  return [
    {
      role: "system",
      content:
        "你是 AuraBot 的记忆抽取模型。只返回 JSON，不要解释。输出 candidates 数组；每项包含 type(trait|preference|event|follow_up), content, topic, importance(0-1), 可选 startsAt/endsAt ISO datetime。若 follow_up 指向上一条 event，可给 relationToPrevious: {relationType, weight}。只写值得长期使用的记忆，避免寒暄。"
    },
    {
      role: "user",
      content: JSON.stringify(input)
    }
  ];
}

function buildMergePrompt(input: MemoryMergePlannerInput): ChatCompletionRequest["messages"] {
  return [
    {
      role: "system",
      content:
        "你是 AuraBot 的记忆合并模型。只返回 JSON，不要解释。判断 candidate 是否与 existingNodes 中某条表达同一稳定事实。若不是同一事实，返回 {\"action\":\"create\"}。若是，返回 {\"action\":\"update_existing\",\"targetNodeId\":\"...\",\"mergedContent\":\"...\",\"importance\":0-1,\"reason\":\"...\"}。targetNodeId 必须来自 existingNodes。不要把相反事实合并。"
    },
    {
      role: "user",
      content: JSON.stringify({
        candidate: input.candidate,
        existingNodes: input.existingNodes.map((node) => ({
          id: node.id,
          type: node.type,
          content: node.content,
          importance: node.importance,
          startsAt: node.startsAt,
          endsAt: node.endsAt
        }))
      })
    }
  ];
}

function buildRelationPrompt(input: MemoryRelationPlannerInput): ChatCompletionRequest["messages"] {
  return [
    {
      role: "system",
      content:
        "你是 AuraBot 的记忆关系模型。只返回 JSON，不要解释。输出 {\"edges\":[]}。每条 edge 包含 targetNodeId, relationType(relates_to|caused_by|scheduled_for|preference_for|opposes), weight(0-1)。targetNodeId 必须来自 existingNodes；只连对后续联想有帮助的边。"
    },
    {
      role: "user",
      content: JSON.stringify({
        newNode: input.newNode,
        existingNodes: input.existingNodes.map((node) => ({
          id: node.id,
          type: node.type,
          content: node.content,
          importance: node.importance,
          startsAt: node.startsAt,
          endsAt: node.endsAt
        }))
      })
    }
  ];
}

function buildRecallPrompt(input: MemoryRecallPlannerInput): ChatCompletionRequest["messages"] {
  return [
    {
      role: "system",
      content:
        "你是 AuraBot 的记忆召回分析模型。只返回 JSON，不要解释。输出 queryTerms、targetTypes 和 rankedNodeIds。queryTerms 是能帮助本地检索的语义词/别名；targetTypes 只能包含 trait/preference/event/follow_up；rankedNodeIds 必须来自 nodes，按相关性从高到低排列。不要发明 node id。"
    },
    {
      role: "user",
      content: JSON.stringify({
        query: input.query,
        summaries: input.summaries.map((summary) => ({
          topic: summary.topic,
          content: summary.content
        })),
        nodes: input.nodes.slice(0, 24).map((node) => ({
          id: node.id,
          type: node.type,
          content: node.content,
          importance: node.importance,
          startsAt: node.startsAt,
          endsAt: node.endsAt,
          updatedAt: node.updatedAt
        }))
      })
    }
  ];
}

function parseJsonFromModel(content: string): unknown {
  const trimmed = content.trim();

  if (trimmed.startsWith("{")) {
    return JSON.parse(trimmed);
  }

  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);

  if (fenced?.[1]) {
    return JSON.parse(fenced[1]);
  }

  throw new Error("Memory model did not return JSON");
}

function dedupeStrings(values: string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
}
