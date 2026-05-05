const SENTENCE_END_PATTERN = /(?:[。！？!?；;～~〜]+|…+)(?:[”’」』）)\]]+)?/g;

export interface SentenceSegment {
  text: string;
  startIndex: number;
  endIndex: number;
}

export interface SplitTextIntoSentencesOptions {
  startIndex?: number;
  flushRemainder?: boolean;
}

export interface SplitTextIntoSentencesResult {
  sentences: SentenceSegment[];
  nextIndex: number;
}

export function splitTextIntoSentences(
  content: string,
  options: SplitTextIntoSentencesOptions = {}
): SplitTextIntoSentencesResult {
  const startIndex = Math.min(Math.max(options.startIndex ?? 0, 0), content.length);
  const sentences: SentenceSegment[] = [];
  let nextIndex = startIndex;
  const segment = content.slice(startIndex);

  SENTENCE_END_PATTERN.lastIndex = 0;
  for (const match of segment.matchAll(SENTENCE_END_PATTERN)) {
    const sentenceEnd = startIndex + match.index + match[0].length;
    const sentence = content.slice(nextIndex, sentenceEnd).trim();

    if (sentence) {
      sentences.push({
        text: sentence,
        startIndex: nextIndex,
        endIndex: sentenceEnd
      });
    }

    nextIndex = sentenceEnd;
  }

  if (options.flushRemainder && nextIndex < content.length) {
    const sentence = content.slice(nextIndex).trim();

    if (sentence) {
      sentences.push({
        text: sentence,
        startIndex: nextIndex,
        endIndex: content.length
      });
    }

    nextIndex = content.length;
  }

  return {
    sentences,
    nextIndex
  };
}
