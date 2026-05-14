import type { Message } from "../../../../shared/schemas/message";
import { splitTextIntoSentences } from "../../../../shared/text/sentences";

export function getMessageDisplaySegments(message: Message): string[] {
  if (message.role !== "assistant") {
    return [message.content];
  }

  const sentences = splitTextIntoSentences(message.content, { flushRemainder: true }).sentences.map(
    (sentence) => sentence.text
  );

  return sentences.length > 0 ? sentences : [message.content];
}
