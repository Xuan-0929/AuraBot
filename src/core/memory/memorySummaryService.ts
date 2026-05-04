import type { MemorySummary } from "../../shared/schemas/memory";

export function selectRecentMemorySummaries(summaries: MemorySummary[], limit = 4): MemorySummary[] {
  return [...summaries]
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
    .slice(0, limit);
}
