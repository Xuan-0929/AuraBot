import type { Message } from "../../../../shared/schemas/message";

export function MessageList({ messages }: { messages: Message[] }) {
  if (messages.length === 0) {
    return (
      <div className="empty-state">
        <h1>AuraBot</h1>
        <p>Ready when you are.</p>
      </div>
    );
  }

  return (
    <div className="message-list" aria-live="polite">
      {messages.map((message) => (
        <article key={message.id} className={`message message-${message.role}`}>
          <span className="message-role">{message.role === "user" ? "You" : "Aura"}</span>
          <p>{message.content}</p>
        </article>
      ))}
    </div>
  );
}
