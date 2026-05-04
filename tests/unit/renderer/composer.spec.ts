/**
 * @vitest-environment jsdom
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { Composer } from "../../../src/renderer/src/features/chat/Composer";

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });

  return { promise, resolve };
}

function changeTextAreaValue(textarea: HTMLTextAreaElement, value: string) {
  const valueSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  valueSetter?.call(textarea, value);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("Composer", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  test("clears the textarea immediately after Enter sends even while onSend is pending", async () => {
    const deferred = createDeferred<void>();
    const onSend = vi.fn(() => deferred.promise);

    await act(async () => {
      root.render(createElement(Composer, { onSend }));
    });

    const textarea = container.querySelector("textarea");
    if (!(textarea instanceof HTMLTextAreaElement)) {
      throw new Error("Composer textarea was not rendered");
    }

    act(() => {
      changeTextAreaValue(textarea, "我喜欢吃火锅。");
    });

    expect(container.querySelector("button")?.disabled).toBe(false);

    act(() => {
      textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    });

    expect(onSend).toHaveBeenCalledWith("我喜欢吃火锅。");
    expect(textarea.value).toBe("");

    deferred.resolve();
    await act(async () => {
      await onSend.mock.results[0]?.value;
    });
  });
});
