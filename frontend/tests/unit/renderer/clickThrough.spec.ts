// @vitest-environment jsdom
/** Checks transparent-window routing and capture release against the existing desktop IPC;
 * the composable must keep held controls interactive even when the pointer leaves. */
import { describe, expect, it, vi } from "vitest";
import { createApp, h, nextTick, ref } from "vue";
import { resolvePetMousePassthroughState, useClickThrough } from "../../../src/features/pet-vue/composables/useClickThrough";

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

it("preserves pointer capture on leave and restores passthrough after release", async () => {
  const setPetMousePassthrough = vi.fn(async () => {});
  Object.assign(window, { aurabot: { setPetMousePassthrough } });
  const interactiveUiHovering = ref(false);
  let controller!: ReturnType<typeof useClickThrough>;
  const host = document.createElement("div");
  const app = createApp({ setup() {
    controller = useClickThrough({ enabled: ref(true), canvasRef: ref(document.createElement("canvas")), interactiveUiHovering });
    return () => h("div");
  } });
  app.mount(host);
  try {
    await controller.handlePointerMove();
    expect(setPetMousePassthrough).toHaveBeenLastCalledWith(true);
    interactiveUiHovering.value = true;
    await nextTick();
    expect(setPetMousePassthrough).toHaveBeenLastCalledWith(false);
    await controller.handlePointerLeave();
    expect(setPetMousePassthrough).toHaveBeenLastCalledWith(false);
    interactiveUiHovering.value = false;
    await nextTick();
    expect(setPetMousePassthrough).toHaveBeenLastCalledWith(true);
  } finally { app.unmount(); }
});

it("resends control hit testing after settings re-enable native passthrough", async () => {
  const setPetMousePassthrough = vi.fn(async () => {});
  Object.assign(window, { aurabot: { setPetMousePassthrough } });
  const enabled = ref(true);
  let controller!: ReturnType<typeof useClickThrough>;
  const host = document.createElement("div");
  const app = createApp({ setup() {
    controller = useClickThrough({ enabled, canvasRef: ref(document.createElement("canvas")), interactiveUiHovering: ref(true) });
    return () => h("div");
  } });
  app.mount(host);
  try {
    await controller.handlePointerMove();
    expect(setPetMousePassthrough).toHaveBeenCalledExactlyOnceWith(false);
    enabled.value = false;
    await nextTick();
    enabled.value = true;
    await nextTick();
    expect(setPetMousePassthrough).toHaveBeenCalledTimes(2);
    expect(setPetMousePassthrough).toHaveBeenLastCalledWith(false);
  } finally { app.unmount(); }
});
