// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DesktopPet, type DesktopPetProps } from "../../../src/renderer/src/features/pet/DesktopPet";
import type { PetCursorPosition } from "../../../src/shared/contracts/ipc";

const live2DProps: Array<{ cursorPosition?: PetCursorPosition }> = [];
vi.mock("../../../src/renderer/src/features/live2d/Live2DModelCanvas", () => ({
  Live2DModelCanvas: (props: { cursorPosition?: PetCursorPosition }) => {
    live2DProps.push(props);

    return <div data-testid="mock-live2d-canvas" />;
  }
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const baseProps: DesktopPetProps = {
  agentStatus: "idle",
  petSettings: {
    clickThroughPet: false,
    displayLanguage: "zh"
  },
  debugPetOverlay: false,
  petCursorPosition: undefined,
  onOpenConsole: vi.fn(async () => undefined),
  onOpenSettings: vi.fn(async () => undefined),
  onSendVoiceInput: vi.fn(async () => undefined),
  onSendTextInput: vi.fn(async () => undefined),
  onSetMousePassthrough: vi.fn(async () => undefined),
  onMovePetWindowBy: vi.fn(async () => undefined),
  onShowPetNotice: vi.fn(),
  onClearPetActivity: vi.fn()
};

describe("DesktopPet hover chat bubble", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    live2DProps.length = 0;
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 600
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows a compact hover chat bubble on the model left before expanding it", async () => {
    await renderPet();
    setModelRect({ left: 260, right: 530 });

    await hoverModel();

    const bubble = container.querySelector<HTMLElement>(".pet-hover-chat");
    expect(bubble?.dataset.side).toBe("left");
    expect(bubble?.dataset.expanded).toBe("false");
    expect(container.querySelector("textarea")).toBeNull();

    await act(async () => {
      container.querySelector<HTMLButtonElement>(".pet-hover-chat-trigger")!.click();
    });

    expect(container.querySelector<HTMLElement>(".pet-hover-chat")?.dataset.expanded).toBe("true");
    expect(container.querySelector("textarea")?.getAttribute("placeholder")).toBe("给主对话发送消息");
  });

  it("moves the hover chat bubble to the model right when the left side is too narrow", async () => {
    await renderPet();
    setModelRect({ left: 28, right: 298 });

    await hoverModel();

    expect(container.querySelector<HTMLElement>(".pet-hover-chat")?.dataset.side).toBe("right");
  });

  it("submits typed bubble text to the main chat sender", async () => {
    const onSendTextInput = vi.fn(async () => undefined);
    await renderPet({ onSendTextInput });
    setModelRect({ left: 260, right: 530 });
    await hoverModel();
    await act(async () => {
      container.querySelector<HTMLButtonElement>(".pet-hover-chat-trigger")!.click();
    });

    const textarea = container.querySelector("textarea");
    expect(textarea).not.toBeNull();

    await act(async () => {
      textarea!.value = "帮我整理今天的任务";
      textarea!.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await act(async () => {
      textarea!.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });

    expect(onSendTextInput).toHaveBeenCalledWith("帮我整理今天的任务");
  });

  it("passes global cursor positions through to the Live2D model without requiring window hover", async () => {
    const petCursorPosition: PetCursorPosition = {
      screenX: 1320,
      screenY: 520,
      localX: 420,
      localY: 220
    };

    await renderPet({ petCursorPosition });

    expect(live2DProps.at(-1)?.cursorPosition).toBe(petCursorPosition);
  });

  it("keeps the hover chat reachable while moving from the model to the bubble", async () => {
    vi.useFakeTimers();
    await renderPet();
    setModelRect({ left: 260, right: 530 });
    await hoverModel();

    const surface = container.querySelector<HTMLElement>("[data-testid='live2d-model-surface']");
    await act(async () => {
      surface!.dispatchEvent(new PointerEvent("pointerout", { bubbles: true }));
    });

    expect(container.querySelector(".pet-hover-chat")).not.toBeNull();

    await act(async () => {
      container.querySelector<HTMLElement>(".pet-hover-chat")!.dispatchEvent(new PointerEvent("pointerover", { bubbles: true }));
      vi.advanceTimersByTime(220);
    });

    expect(container.querySelector(".pet-hover-chat")).not.toBeNull();
    vi.useRealTimers();
  });

  it("opens the product settings window from pet quick controls", async () => {
    const onOpenSettings = vi.fn(async () => undefined);

    await renderPet({ onOpenSettings });
    setModelRect({ left: 260, right: 530 });
    await hoverModel();

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[aria-label="打开 AuraBot 设置"]')!.click();
    });

    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  async function renderPet(overrides: Partial<DesktopPetProps> = {}) {
    await act(async () => {
      root.render(<DesktopPet {...baseProps} {...overrides} />);
    });
  }

  function setModelRect(rect: { left: number; right: number }) {
    const button = container.querySelector<HTMLElement>(".desktop-pet-model-button");
    vi.spyOn(button!, "getBoundingClientRect").mockReturnValue({
      left: rect.left,
      right: rect.right,
      top: 60,
      bottom: 360,
      width: rect.right - rect.left,
      height: 300,
      x: rect.left,
      y: 60,
      toJSON: () => ({})
    } as DOMRect);
  }

  async function hoverModel() {
    const surface = container.querySelector<HTMLElement>("[data-testid='live2d-model-surface']");
    await act(async () => {
      surface!.dispatchEvent(new PointerEvent("pointerover", { bubbles: true }));
    });
  }
});
