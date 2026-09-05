/** Pure UI delivery helpers retained for sentence presentation tests.
 * Production IPC is registered by main/index.ts; this module owns no IPC, business services or Memory.
 */
import type { SendChatMessageInput, SendChatMessageResult, AgentStatus } from '@shared/contracts/chat';
import type { Message } from '@shared/schemas/message';
import type { PetActivity } from '@shared/contracts/pet';
import { splitTextIntoSentences } from '@shared/text/sentences';
type ChatOwnerWindowController={recordUserActivity():void;sendChatMessage(input:SendChatMessageInput):Promise<SendChatMessageResult>;broadcastAgentStatus(status:AgentStatus):void};
export async function sendChatMessageThroughOwner(
  owner: ChatOwnerWindowController,
  input: SendChatMessageInput
): Promise<SendChatMessageResult> {
  owner.recordUserActivity();
  const result = await owner.sendChatMessage(input);
  owner.broadcastAgentStatus(result.agentStatus);
  return result;
}

export interface BuildAssistantMessagePetActivitiesOptions {
  startIndex: number;
  flushRemainder?: boolean;
}

export interface BuildAssistantMessagePetActivitiesResult {
  activities: PetActivity[];
  nextIndex: number;
}

export function buildAssistantMessagePetActivities(
  message: Message,
  options: BuildAssistantMessagePetActivitiesOptions
): BuildAssistantMessagePetActivitiesResult {
  const startIndex = Math.min(Math.max(options.startIndex, 0), message.content.length);
  if (message.role !== "assistant" || !message.content.trim()) {
    return { activities: [], nextIndex: startIndex };
  }
  const result = splitTextIntoSentences(message.content, {
    startIndex,
    flushRemainder: options.flushRemainder
  });
  const createdAt = new Date().toISOString();
  return {
    activities: result.sentences.map((sentence, index) => ({
      id: `chat-reply-${message.id}-${index}-${sentence.endIndex}`,
      kind: "chat_reply",
      message: sentence.text,
      createdAt
    })),
    nextIndex: result.nextIndex
  };
}
