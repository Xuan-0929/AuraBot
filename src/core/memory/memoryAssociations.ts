import type { MemoryNode, MemoryWriteRequest } from "../../shared/schemas/memory";

type MemoryWriteEdge = MemoryWriteRequest["edges"][number];

export interface BuildAssociativeMemoryEdgesInput {
  newNode: MemoryWriteRequest["node"];
  existingNodes: MemoryNode[];
}

const STOP_TERMS = new Set([
  "用户",
  "提到",
  "一个",
  "时间",
  "相关",
  "事件",
  "需要",
  "跟进",
  "提醒",
  "喜欢",
  "讨厌",
  "明天",
  "后天",
  "今晚",
  "下周",
  "别忘",
  "记得"
]);

export function buildAssociativeMemoryEdges(input: BuildAssociativeMemoryEdgesInput): MemoryWriteEdge[] {
  const newConcepts = deriveMemoryConcepts(input.newNode.content);
  const edges: MemoryWriteEdge[] = [];

  if (newConcepts.size === 0) {
    return edges;
  }

  for (const existingNode of input.existingNodes) {
    if (existingNode.type === input.newNode.type && existingNode.content === input.newNode.content) {
      continue;
    }

    const existingConcepts = deriveMemoryConcepts(existingNode.content);
    const sharedConcepts = [...newConcepts].filter((concept) => existingConcepts.has(concept));

    if (sharedConcepts.length === 0) {
      continue;
    }

    edges.push({
      targetNodeId: existingNode.id,
      relationType: chooseRelationType(input.newNode.type, existingNode.type),
      weight: chooseRelationWeight(input.newNode.type, existingNode.type, sharedConcepts)
    });
  }

  return dedupeEdges(edges);
}

export function deriveMemoryConcepts(content: string): Set<string> {
  const normalized = content
    .replace(/^用户提到一个时间相关事件：/, "")
    .replace(/^需要跟进或提醒用户：/, "")
    .replace(/^用户/, "")
    .replace(/[，。,.！？!?；;：:\n]/g, " ");
  const concepts = new Set<string>();

  for (const match of normalized.matchAll(/(?:喜欢|讨厌)(?:吃|玩|看)?([^\s，。,.！？!?；;：:]+)/g)) {
    addConcept(concepts, match[1]);
  }

  for (const match of normalized.matchAll(/(?:去|吃|玩|看|参加|开|考|面试)([^\s，。,.！？!?；;：:]+)/g)) {
    addConcept(concepts, match[1]);
  }

  for (const rawToken of normalized.split(/\s+/)) {
    addConcept(concepts, rawToken);
  }

  return concepts;
}

function chooseRelationType(
  newType: MemoryWriteRequest["node"]["type"],
  existingType: MemoryNode["type"]
): MemoryWriteEdge["relationType"] {
  if (
    (newType === "event" && existingType === "preference") ||
    (newType === "follow_up" && existingType === "preference") ||
    (newType === "preference" && (existingType === "event" || existingType === "follow_up"))
  ) {
    return "preference_for";
  }

  return "relates_to";
}

function chooseRelationWeight(
  newType: MemoryWriteRequest["node"]["type"],
  existingType: MemoryNode["type"],
  sharedConcepts: string[]
): number {
  const base =
    (newType === "event" && existingType === "preference") ||
    (newType === "follow_up" && existingType === "preference")
      ? 0.86
      : 0.68;
  const conceptBoost = Math.min(sharedConcepts.length * 0.04, 0.12);

  return Math.min(base + conceptBoost, 0.95);
}

function addConcept(concepts: Set<string>, rawValue: string | undefined): void {
  const value = cleanupConcept(rawValue);

  if (!value || STOP_TERMS.has(value)) {
    return;
  }

  concepts.add(value);
}

function cleanupConcept(rawValue: string | undefined): string {
  if (!rawValue) {
    return "";
  }

  return rawValue
    .replace(/^(吃|玩|看|去|和|跟|同|要|有)/, "")
    .replace(/(这件事|这个|那个|一件事|来着|顺便|注意什么|什么)$/g, "")
    .trim();
}

function dedupeEdges(edges: MemoryWriteEdge[]): MemoryWriteEdge[] {
  const seen = new Set<string>();
  const result: MemoryWriteEdge[] = [];

  for (const edge of edges) {
    const key = `${edge.targetNodeId}:${edge.relationType}`;
    if (seen.has(key)) {
      continue;
    }

    seen.add(key);
    result.push(edge);
  }

  return result;
}
