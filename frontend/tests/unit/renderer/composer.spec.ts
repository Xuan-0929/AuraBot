/**
 * Composer renderer 测试：覆盖发送、换行、自适应高度、状态指示与普通附件选择。
 * Agent 状态只使用当前 chat 生命周期，不构造已删除的执行专属状态。
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

    expect(onSend).toHaveBeenCalledWith("send me", []);
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

  it("shows only the agent status light and label in the action rail", () => {
    const onSend = vi.fn(async () => undefined);
    const app = createApp(Composer, { agentStatus: "thinking", displayLanguage: "en", onSend });
    app.mount(container);

    expect(container.querySelector(".composer-status-dot-thinking")).not.toBeNull();
    expect(container.querySelector(".composer-presence")?.textContent).toContain("thinking");
    expect(container.querySelector(".composer-presence-mark")).toBeNull();
    app.unmount();
  });

  it("uses a plain plus glyph for the attachment action", () => {
    const onSend = vi.fn(async () => undefined);
    const app = createApp(Composer, { displayLanguage: "en", onSend });
    app.mount(container);

    expect(container.querySelector(".composer-add-icon")?.textContent).toBe("+");
    app.unmount();
  });

  it("shows selected file names and falls back to the path basename", async () => {
    vi.stubGlobal("window", {
      aurabot: {
        getPathForFile: vi.fn(() => "/Users/xuan/Documents/very-long-report.pdf")
      }
    });
    const onSend = vi.fn(async () => undefined);
    const app = createApp(Composer, { displayLanguage: "en", onSend });
    app.mount(container);

    const input = container.querySelector<HTMLInputElement>("input[type='file']");
    if (!input) {
      throw new Error("file input not found");
    }

    Object.defineProperty(input, "files", {
      configurable: true,
      value: [
        {
          name: "",
          type: "application/pdf",
          size: 42
        }
      ]
    });
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await Promise.resolve();
    await nextTick();

    const name = container.querySelector(".composer-attachment-name");
    expect(name?.textContent?.trim()).toBe("very-l...eport.pdf");
    expect(name?.getAttribute("title")).toBe("very-long-report.pdf");
    app.unmount();
  });

  it("shortens long selected file names in the middle", async () => {
    vi.stubGlobal("window", {
      aurabot: {
        getPathForFile: vi.fn(() => "/Users/xuan/Documents/product-requirements-final-version-12.pdf")
      }
    });
    const onSend = vi.fn(async () => undefined);
    const app = createApp(Composer, { displayLanguage: "en", onSend });
    app.mount(container);

    const input = container.querySelector<HTMLInputElement>("input[type='file']");
    if (!input) {
      throw new Error("file input not found");
    }

    Object.defineProperty(input, "files", {
      configurable: true,
      value: [
        {
          name: "product-requirements-final-version-12.pdf",
          type: "application/pdf",
          size: 42
        }
      ]
    });
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await Promise.resolve();
    await nextTick();

    const name = container.querySelector(".composer-attachment-name");
    expect(name?.textContent?.trim()).toBe("produc...on-12.pdf");
    expect(name?.getAttribute("title")).toBe("product-requirements-final-version-12.pdf");
    app.unmount();
  });

  it("uses a tighter middle abbreviation for wide CJK file names", async () => {
    vi.stubGlobal("window", {
      aurabot: {
        getPathForFile: vi.fn(() => "/Users/xuan/Documents/智能2402-20260517-宇轩-任务材料.pdf")
      }
    });
    const onSend = vi.fn(async () => undefined);
    const app = createApp(Composer, { displayLanguage: "en", onSend });
    app.mount(container);

    const input = container.querySelector<HTMLInputElement>("input[type='file']");
    if (!input) {
      throw new Error("file input not found");
    }

    Object.defineProperty(input, "files", {
      configurable: true,
      value: [
        {
          name: "智能2402-20260517-宇轩-任务材料.pdf",
          type: "application/pdf",
          size: 42
        }
      ]
    });
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await Promise.resolve();
    await nextTick();

    const name = container.querySelector(".composer-attachment-name");
    expect(name?.textContent?.trim()).toBe("智能24...材料.pdf");
    expect(name?.getAttribute("title")).toBe("智能2402-20260517-宇轩-任务材料.pdf");
    app.unmount();
  });
});
