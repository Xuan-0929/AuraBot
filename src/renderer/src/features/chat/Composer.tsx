import { FormEvent, KeyboardEvent, useState } from "react";

export function Composer({ onSend }: { onSend(content: string): Promise<void> }) {
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function sendContent(nextContent: string) {
    if (!nextContent.trim() || submitting) {
      return;
    }

    setSubmitting(true);
    try {
      await onSend(nextContent);
      setContent("");
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
        placeholder="Say something"
        rows={2}
      />
      <button type="submit" disabled={submitting || !content.trim()}>
        Send
      </button>
    </form>
  );
}
