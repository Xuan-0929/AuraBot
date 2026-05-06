import { FormEvent, KeyboardEvent, useState } from "react";
import type { PetSettings } from "../../../../shared/schemas/petSettings";
import { t } from "../../i18n";

export function Composer({
  displayLanguage = "en",
  onSend
}: {
  displayLanguage?: PetSettings["displayLanguage"];
  onSend(content: string): Promise<void>;
}) {
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function sendContent(nextContent: string) {
    if (!nextContent.trim() || submitting) {
      return;
    }

    setSubmitting(true);
    setContent("");
    try {
      await onSend(nextContent);
    } catch (error) {
      setContent((currentContent) => currentContent || nextContent);
      throw error;
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await sendContent(content);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) {
      return;
    }

    if (event.ctrlKey) {
      event.preventDefault();
      insertLineBreak(event.currentTarget);
      return;
    }

    event.preventDefault();
    void sendContent(event.currentTarget.value);
  }

  function insertLineBreak(textarea: HTMLTextAreaElement) {
    const selectionStart = textarea.selectionStart ?? textarea.value.length;
    const selectionEnd = textarea.selectionEnd ?? textarea.value.length;

    textarea.setRangeText("\n", selectionStart, selectionEnd, "end");
    setContent(textarea.value);
  }

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <textarea
        value={content}
        onChange={(event) => setContent(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={t(displayLanguage, "saySomething")}
        rows={2}
      />
      <button type="submit" disabled={submitting || !content.trim()}>
        {t(displayLanguage, "send")}
      </button>
    </form>
  );
}
