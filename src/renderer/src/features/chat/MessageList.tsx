import { useEffect, useRef } from "react";
import type { Message, MessageAttachment } from "../../../../shared/schemas/message";

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
      {messages.map((message) => (
        <article key={message.id} className={`message message-${message.role}`}>
          <span className="message-role">{message.role === "user" ? "You" : "Aura"}</span>
          <p>{message.content}</p>
          {(message.attachments ?? []).length > 0 ? (
            <MessageAttachments attachments={message.attachments ?? []} />
          ) : null}
        </article>
      ))}
    </div>
  );
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
