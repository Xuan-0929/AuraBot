import { describe, expect, test } from "vitest";
import {
  calculateFocusedLive2DViewTransform,
  calculateNextLive2DViewScale
} from "../../../src/renderer/src/features/live2d/live2dViewScale";

describe("calculateNextLive2DViewScale", () => {
  test("zooms the Live2D model in for upward pinch-wheel deltas", () => {
    expect(calculateNextLive2DViewScale(1, -100)).toBeCloseTo(1.1, 4);
  });

  test("zooms the Live2D model out for downward pinch-wheel deltas", () => {
    expect(calculateNextLive2DViewScale(1, 100)).toBeCloseTo(0.9, 4);
  });

  test("clamps gesture zoom to a practical desktop-pet range", () => {
    expect(calculateNextLive2DViewScale(0.35, 1000)).toBe(0.35);
    expect(calculateNextLive2DViewScale(2.4, -1000)).toBe(2.4);
  });
});

describe("calculateFocusedLive2DViewTransform", () => {
  test("keeps the focused model point under the cursor while zooming", () => {
    const next = calculateFocusedLive2DViewTransform({
      hostSize: { width: 270, height: 380 },
      modelSize: { width: 900, height: 1600 },
      compact: true,
      current: { scale: 1, offset: { x: 0, y: 0 } },
      focalPoint: { x: 135, y: 100 },
      wheelDeltaY: -100
    });

    expect(next.scale).toBeCloseTo(1.1, 4);
    expect(next.offset.x).toBeCloseTo(0, 4);
    expect(next.offset.y).toBeCloseTo(9, 3);
  });
});
