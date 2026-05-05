import { useEffect, useRef } from "react";
import type { Message, MessageAttachment } from "../../../../shared/schemas/message";
import { splitTextIntoSentences } from "../../../../shared/text/sentences";

export function MessageList({ messages }: { messages: Message[] }) {
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const listElement = listRef.current;

    if (!listElement) {
      return;
    }

    listElement.scrollTop = listElement.scrollHeight;
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="empty-state">
        <h1>AuraBot</h1>
        <p>Ready when you are.</p>
      </div>
    );
  }

  return (
    <div ref={listRef} className="message-list" aria-label="Conversation messages" aria-live="polite" tabIndex={0}>
      {messages.flatMap((message) => renderMessageBubbles(message))}
    </div>
  );
}

function renderMessageBubbles(message: Message) {
  const segments = getMessageDisplaySegments(message);

  return segments.map((segment, index) => {
    const isFirstSegment = index === 0;
    const isLastSegment = index === segments.length - 1;
    const attachments = isLastSegment ? (message.attachments ?? []) : [];

    return (
      <article key={`${message.id}-${index}`} className={`message message-${message.role}`}>
        {isFirstSegment ? <span className="message-role">{message.role === "user" ? "You" : "Aura"}</span> : null}
        <p>{segment}</p>
        {attachments.length > 0 ? <MessageAttachments attachments={attachments} /> : null}
      </article>
    );
  });
}

function getMessageDisplaySegments(message: Message): string[] {
  if (message.role !== "assistant") {
    return [message.content];
  }

  const sentences = splitTextIntoSentences(message.content, { flushRemainder: true }).sentences.map(
    (sentence) => sentence.text
  );

  return sentences.length > 0 ? sentences : [message.content];
}

function MessageAttachments({ attachments }: { attachments: MessageAttachment[] }) {
  return (
    <div className="message-attachments">
      {attachments.map((attachment) => (
        <div key={attachment.id} className="message-attachment">
          <span className="message-attachment-name">{attachment.name}</span>
          <span className="message-attachment-actions">
            <button
              type="button"
              onClick={() =>
                void window.aurabot.openTaskResultFile({
                  taskId: attachment.taskId,
                  path: attachment.path
                })
              }
            >
              打开文件
            </button>
            <button
              type="button"
              onClick={() =>
                void window.aurabot.showTaskResultFileInFolder({
                  taskId: attachment.taskId,
                  path: attachment.path
                })
              }
            >
              所在文件夹
            </button>
          </span>
        </div>
      ))}
    </div>
  );
}
