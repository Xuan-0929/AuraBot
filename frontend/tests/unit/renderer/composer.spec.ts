/**
 * Composer renderer 测试：覆盖纯文本发送、换行、自适应高度与发送按钮的状态提示；状态文字不再单独占用输入栏。
 * 新附件输入在 Task Runtime 空窗期已移除，组件不得暴露文件选择或拖放入口。
 */
// @vitest-environment jsdom

import { createApp, nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Composer from "../../../src/features/chat/Composer.vue";

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

  it("sends on Enter and inserts a line break on Shift+Enter", async () => {
    const onSend = vi.fn(async () => undefined);
    const app = createApp(Composer, { displayLanguage: "en", onSend });
    app.mount(container);

    const textarea = container.querySelector("textarea");
    if (!textarea) {
      throw new Error("textarea not found");
    }

    textarea.value = "hello";
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, shiftKey: true }));
    expect(textarea.value).toBe("hello\n");

    textarea.value = "send me";
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await Promise.resolve();

    expect(onSend).toHaveBeenCalledWith("send me");
    app.unmount();
  });

  it("keeps send discoverable, blocks empty input and restores failed text", async () => {
    const onSend = vi.fn(async () => { throw new Error("offline"); });
    const app = createApp(Composer, { displayLanguage: "en", onSend });
    app.mount(container);
    const send = container.querySelector<HTMLButtonElement>("button[type=submit]");
    expect(send).not.toBeNull();
    expect(send!.disabled).toBe(true);
    const input = container.querySelector("textarea")!;
    input.value = "hello";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await nextTick();
    expect(send!.disabled).toBe(false);
    input.dispatchEvent(new KeyboardEvent("keydown", {key:"Enter", isComposing:true, bubbles:true}));
    expect(onSend).not.toHaveBeenCalled();
    send!.click();
    await vi.waitFor(() => expect(input.value).toBe("hello"));
    expect(onSend).toHaveBeenCalledOnce();
    app.unmount();
  });

  it("grows with its content and caps its height", async () => {
    const onSend = vi.fn(async () => undefined);
    const app = createApp(Composer, { displayLanguage: "en", onSend });
    app.mount(container);

    const textarea = container.querySelector("textarea");
    if (!textarea) {
      throw new Error("textarea not found");
    }

    Object.defineProperty(textarea, "scrollHeight", { configurable: true, value: 152 });
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    expect(textarea.style.height).toBe("152px");

    Object.defineProperty(textarea, "scrollHeight", { configurable: true, value: 260 });
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    expect(textarea.style.height).toBe("198px");
    app.unmount();
  });

  it("describes the agent status on the send button without a separate status row", () => {
    const onSend = vi.fn(async () => undefined);
    const app = createApp(Composer, { agentStatus: "thinking", displayLanguage: "en", onSend });
    app.mount(container);

    const send = container.querySelector("button[type=submit]")!;
    expect(send.getAttribute("aria-description")).toContain("thinking");
    expect(send.getAttribute("title")).toContain("thinking");
    expect(send.getAttribute("data-status")).toBe("thinking");
    expect(container.querySelector(".composer-presence")).toBeNull();
    app.unmount();
  });

  it("does not expose file selection or attachment actions", () => {
    const onSend = vi.fn(async () => undefined);
    const app = createApp(Composer, { displayLanguage: "en", onSend });
    app.mount(container);

    expect(container.querySelector("input[type='file']")).toBeNull();
    expect(container.querySelector(".composer-attach-button")).toBeNull();
    expect(container.querySelector(".composer-attachments")).toBeNull();
    app.unmount();
  });
});
