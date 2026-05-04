import { useEffect, useRef } from "react";
import type { Message } from "../../../../shared/schemas/message";

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
        </article>
      ))}
    </div>
  );
}
