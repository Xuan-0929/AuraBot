import type { MemoryWriteRequest } from "../../../shared/src/schemas/memory";

export interface ExtractedMemoryCandidate {
  node: MemoryWriteRequest["node"];
  topic: string;
  relationToPrevious?: MemoryWriteRequest["edges"][number];
}

export interface MemoryExtractionInput {
  message: string;
  nowIso: string;
  timezone: string;
}

export interface MemoryExtractor {
  extract(input: MemoryExtractionInput): Promise<ExtractedMemoryCandidate[]>;
}

export class DeterministicMemoryExtractor implements MemoryExtractor {
  async extract(input: MemoryExtractionInput): Promise<ExtractedMemoryCandidate[]> {
    return extractMemoryCandidates(input.message);
  }
}

const PUNCTUATION_BOUNDARY = /[，。,.！？!?；;\n]/;
const TIME_HINT_PATTERN = /(今晚|明天|后天|下周[一二三四五六日天]?|周[一二三四五六日天]|下个月|月底|月初)/;
const EVENT_HINT_PATTERN = /(要|有|去|看|参加|考试|面试|演唱会|交作业|开会|提醒)/;

export function extractMemoryCandidates(message: string): ExtractedMemoryCandidate[] {
  const trimmed = message.trim();

  if (!trimmed) {
    return [];
  }

  const candidates: ExtractedMemoryCandidate[] = [];
  candidates.push(...extractPreferenceCandidates(trimmed));

  if (TIME_HINT_PATTERN.test(trimmed) && EVENT_HINT_PATTERN.test(trimmed)) {
    candidates.push({
      topic: "events",
      node: {
        type: "event",
        content: `用户提到一个时间相关事件：${trimmed}`,
        importance: 0.84
      }
    });

    if (/提醒|别忘|记得/.test(trimmed)) {
      candidates.push({
        topic: "follow_ups",
        node: {
          type: "follow_up",
          content: `需要跟进或提醒用户：${trimmed}`,
          importance: 0.78
        },
        relationToPrevious: {
          targetNodeId: "",
          relationType: "scheduled_for",
          weight: 0.9
        }
      });
    }
  }

  return candidates;
}

function extractPreferenceCandidates(message: string): ExtractedMemoryCandidate[] {
  const candidates: ExtractedMemoryCandidate[] = [];
  const clauses = message.split(PUNCTUATION_BOUNDARY).map((clause) => clause.trim());

  for (const clause of clauses) {
    const likeMatch = clause.match(/(?:我)?喜欢(?:吃|玩|看)?(.+)/);
    if (likeMatch?.[1]) {
      const object = cleanupPreferenceObject(likeMatch[1]);

      if (object) {
        candidates.push({
          topic: "preferences",
          node: {
            type: "preference",
            content: `用户喜欢${object}`,
            importance: 0.72
          }
        });
      }
    }

    const dislikeMatch = clause.match(/(?:我)?讨厌(.+)/);
    if (dislikeMatch?.[1]) {
      const object = cleanupPreferenceObject(dislikeMatch[1]);

      if (object) {
        candidates.push({
          topic: "preferences",
          node: {
            type: "preference",
            content: `用户讨厌${object}`,
            importance: 0.68
          }
        });
      }
    }
  }

  return candidates;
}

function cleanupPreferenceObject(value: string): string {
  return value
    .replace(/^(吃|玩|看)/, "")
    .replace(/(来着|这件事|这个|那个)$/g, "")
    .trim();
}

export function shouldAskMemoryRecall(message: string): boolean {
  return /(记得|记住|来着|什么|哪天|啥|有什么事|喜欢什么|讨厌什么)/.test(message);
}
