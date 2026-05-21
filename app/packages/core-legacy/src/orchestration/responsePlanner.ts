import type { MemoryNode } from "../../../shared/src/schemas/memory";
import type { Soul } from "../../../shared/src/schemas/profile";
import { shouldAskMemoryRecall } from "../memory/memoryExtractor";

export interface PlanAssistantResponseInput {
  userContent: string;
  soul?: Soul;
  writtenMemories: MemoryNode[];
  recalledMemories: MemoryNode[];
  dueFollowUps?: MemoryNode[];
}

export function planAssistantResponse(input: PlanAssistantResponseInput): string {
  const dueFollowUps = dedupeMemories(input.dueFollowUps ?? []);
  const recalled = dedupeMemories(input.recalledMemories);
  const written = dedupeMemories(input.writtenMemories);

  if (dueFollowUps.length > 0) {
    return `我提醒你一下：${formatMemoryList(dueFollowUps)}。`;
  }

  if (recalled.length > 0 && shouldAskMemoryRecall(input.userContent)) {
    return `我记得：${formatMemoryList(recalled)}。`;
  }

  if (written.length > 0) {
    return `我记住了：${formatMemoryList(written)}。`;
  }

  return "嗯，我在。你继续说，我会把这段关系上下文接住。";
}

function formatMemoryList(memories: MemoryNode[]): string {
  return memories.map((memory) => cleanMemoryContent(memory.content)).join("；");
}

function cleanMemoryContent(content: string): string {
  return content
    .replace(/^用户提到一个时间相关事件：/, "")
    .replace(/^需要跟进或提醒用户：/, "")
    .replace(/^用户/, "你")
    .trim();
}

function dedupeMemories(memories: MemoryNode[]): MemoryNode[] {
  const seen = new Set<string>();
  const result: MemoryNode[] = [];

  for (const memory of memories) {
    if (seen.has(memory.id)) {
      continue;
    }

    seen.add(memory.id);
    result.push(memory);
  }

  return result;
}
