// @vitest-environment jsdom

import { createApp } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Composer from "../../../src/renderer/src/features/chat/Composer.vue";

describe("Composer", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
  });

  afterEach(() => {
    container.remove();
    vi.restoreAllMocks();
  });

  it("sends on Enter and inserts a line break on Ctrl+Enter", async () => {
    const onSend = vi.fn(async () => undefined);
    const app = createApp(Composer, { displayLanguage: "en", onSend });
    app.mount(container);

    const textarea = container.querySelector("textarea");
    if (!textarea) {
      throw new Error("textarea not found");
    }

    textarea.value = "hello";
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, ctrlKey: true }));
    expect(textarea.value).toBe("hello\n");

    textarea.value = "send me";
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await Promise.resolve();

    expect(onSend).toHaveBeenCalledWith("send me");
    app.unmount();
  });
});
