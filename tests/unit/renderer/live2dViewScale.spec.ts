import { describe, expect, test } from "vitest";
import { calculateNextLive2DViewScale } from "../../../src/renderer/src/features/live2d/live2dViewScale";

describe("calculateNextLive2DViewScale", () => {
  test("zooms the Live2D model in for upward pinch-wheel deltas", () => {
    expect(calculateNextLive2DViewScale(1, -100)).toBeCloseTo(1.1, 4);
  });

  test("zooms the Live2D model out for downward pinch-wheel deltas", () => {
    expect(calculateNextLive2DViewScale(1, 100)).toBeCloseTo(0.9, 4);
  });

  test("clamps the Live2D model view scale to AIRI-compatible bounds", () => {
    expect(calculateNextLive2DViewScale(0.01, 1000)).toBe(0.01);
    expect(calculateNextLive2DViewScale(3, -1000)).toBe(3);
  });
});
