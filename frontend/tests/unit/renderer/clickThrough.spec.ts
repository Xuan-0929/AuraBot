import { describe, expect, it } from "vitest";
import { resolvePetMousePassthroughState } from "../../../src/features/pet-vue/composables/useClickThrough";

describe("resolvePetMousePassthroughState", () => {
  it("keeps click-through enabled over the model surface", () => {
    expect(
      resolvePetMousePassthroughState({
        canvasReady: true,
        enabled: true,
        interactiveUiHovering: false,
        resizeUiHovering: false
      })
    ).toBe(true);
  });

  it("keeps controls and resize handles interactive while click-through is enabled", () => {
    expect(
      resolvePetMousePassthroughState({
        canvasReady: true,
        enabled: true,
        interactiveUiHovering: true,
        resizeUiHovering: false
      })
    ).toBe(false);
    expect(
      resolvePetMousePassthroughState({
        canvasReady: true,
        enabled: true,
        interactiveUiHovering: false,
        resizeUiHovering: true
      })
    ).toBe(false);
  });

  it("does not change passthrough before click-through is active and ready", () => {
    expect(
      resolvePetMousePassthroughState({
        canvasReady: true,
        enabled: false,
        interactiveUiHovering: false,
        resizeUiHovering: false
      })
    ).toBeUndefined();
    expect(
      resolvePetMousePassthroughState({
        canvasReady: false,
        enabled: true,
        interactiveUiHovering: false,
        resizeUiHovering: false
      })
    ).toBeUndefined();
  });
});
