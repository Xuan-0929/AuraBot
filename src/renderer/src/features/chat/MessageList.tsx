import { useEffect, useRef } from "react";
import type { Message, MessageAttachment } from "../../../../shared/schemas/message";
import type { PetSettings } from "../../../../shared/schemas/petSettings";
import { splitTextIntoSentences } from "../../../../shared/text/sentences";
import { t } from "../../i18n";

export function MessageList({
  messages,
  displayLanguage = "en"
}: {
  messages: Message[];
  displayLanguage?: PetSettings["displayLanguage"];
}) {
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
        <span className="empty-state-kicker">{t(displayLanguage, "mainConversation")}</span>
        <p>{t(displayLanguage, "readyWhenYouAre")}</p>
      </div>
    );
  }

  return (
    <div ref={listRef} className="message-list" aria-label={t(displayLanguage, "conversationMessages")} aria-live="polite" tabIndex={0}>
      {messages.flatMap((message) => renderMessageBubbles(message, displayLanguage))}
    </div>
  );
}

function renderMessageBubbles(message: Message, displayLanguage: PetSettings["displayLanguage"]) {
  const segments = getMessageDisplaySegments(message);

  return segments.map((segment, index) => {
    const isFirstSegment = index === 0;
    const isLastSegment = index === segments.length - 1;
    const attachments = isLastSegment ? (message.attachments ?? []) : [];

    return (
      <article key={`${message.id}-${index}`} className={`message message-${message.role}`}>
        {isFirstSegment ? <span className="message-role">{message.role === "user" ? t(displayLanguage, "you") : t(displayLanguage, "aura")}</span> : null}
        <p>{segment}</p>
        {attachments.length > 0 ? <MessageAttachments attachments={attachments} displayLanguage={displayLanguage} /> : null}
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

function MessageAttachments({
  attachments,
  displayLanguage
}: {
  attachments: MessageAttachment[];
  displayLanguage: PetSettings["displayLanguage"];
}) {
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
              {t(displayLanguage, "openFile")}
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
              {t(displayLanguage, "showInFolder")}
            </button>
          </span>
        </div>
      ))}
    </div>
  );
}
