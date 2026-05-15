import { describe, expect, it } from "vitest";
import { resolvePetMousePassthroughState } from "../../../src/renderer/src/features/pet-vue/composables/useClickThrough";

describe("resolvePetMousePassthroughState", () => {
  it("keeps click-through enabled over the model surface", () => {
    expect(
      resolvePetMousePassthroughState({
        canvasReady: true,
        enabled: true,
        interactiveUiHovering: false,
        overResizeCorner: false
      })
    ).toBe(true);
  });

  it("keeps controls and resize corners interactive while click-through is enabled", () => {
    expect(
      resolvePetMousePassthroughState({
        canvasReady: true,
        enabled: true,
        interactiveUiHovering: true,
        overResizeCorner: false
      })
    ).toBe(false);
    expect(
      resolvePetMousePassthroughState({
        canvasReady: true,
        enabled: true,
        interactiveUiHovering: false,
        overResizeCorner: true
      })
    ).toBe(false);
  });

  it("does not change passthrough before click-through is active and ready", () => {
    expect(
      resolvePetMousePassthroughState({
        canvasReady: true,
        enabled: false,
        interactiveUiHovering: false,
        overResizeCorner: false
      })
    ).toBeUndefined();
    expect(
      resolvePetMousePassthroughState({
        canvasReady: false,
        enabled: true,
        interactiveUiHovering: false,
        overResizeCorner: false
      })
    ).toBeUndefined();
  });
});
