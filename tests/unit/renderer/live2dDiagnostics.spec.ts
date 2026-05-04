import { describe, expect, test } from "vitest";
import {
  calculateLive2DPlacement,
  resolveLive2DHostSize
} from "../../../src/renderer/src/features/live2d/live2dDiagnostics";

describe("resolveLive2DHostSize", () => {
  test("uses client dimensions when the host has already been laid out", () => {
    expect(resolveLive2DHostSize({ clientWidth: 240, clientHeight: 360 }, { width: 270, height: 380 })).toEqual({
      width: 240,
      height: 360
    });
  });

  test("falls back to the bounding rect before client dimensions are ready", () => {
    expect(
      resolveLive2DHostSize(
        {
          clientWidth: 0,
          clientHeight: 0,
          getBoundingClientRect: () => ({ width: 250.4, height: 370.2 })
        },
        { width: 270, height: 380 }
      )
    ).toEqual({
      width: 250,
      height: 370
    });
  });

  test("uses a non-zero fallback to avoid a blank first render", () => {
    expect(resolveLive2DHostSize({ clientWidth: 0, clientHeight: 0 }, { width: 270, height: 380 })).toEqual({
      width: 270,
      height: 380
    });
  });
});

describe("calculateLive2DPlacement", () => {
  test("centers and anchors the model at the bottom of the host", () => {
    expect(
      calculateLive2DPlacement({
        hostSize: { width: 270, height: 380 },
        modelSize: { width: 900, height: 1600 },
        compact: true
      })
    ).toEqual({
      x: 135,
      y: 380,
      scale: 0.25175
    });
  });
});
