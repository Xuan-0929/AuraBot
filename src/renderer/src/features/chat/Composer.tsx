import { FormEvent, useState } from "react";

export function Composer({ onSend }: { onSend(content: string): Promise<void> }) {
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!content.trim() || submitting) {
      return;
    }

    setSubmitting(true);
    await onSend(content);
    setContent("");
    setSubmitting(false);
  }

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <textarea
        value={content}
        onChange={(event) => setContent(event.target.value)}
        placeholder="Say something"
        rows={2}
      />
      <button type="submit" disabled={submitting || !content.trim()}>
        Send
      </button>
    </form>
  );
}
