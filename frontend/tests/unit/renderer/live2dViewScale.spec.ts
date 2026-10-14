/** Verifies shared zoom bounds and cursor anchoring for portrait framing.
 * Placement containment separately constrains full-model framing; persisted transforms retain the
 * user's zoom intent when switching between the two Profile framing preferences.
 */
import { describe, expect, test } from "vitest";
import {calculateLive2DPlacement} from "../../../src/features/live2d/live2dDiagnostics";
import {
  calculateFocusedLive2DViewTransform,
  calculateNextLive2DViewScale
} from "../../../src/features/live2d/live2dViewScale";

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
      showFullModel: false,
      current: { scale: 1, offset: { x: 0, y: 0 } },
      focalPoint: { x: 135, y: 100 },
      wheelDeltaY: -100
    });

    expect(next.scale).toBeCloseTo(1.1, 4);
    expect(next.offset.x).toBeCloseTo(0, 4);
    const args={hostSize:{width:270,height:380},modelSize:{width:900,height:1600},compact:true,showFullModel:false};
    const before=calculateLive2DPlacement(args),after=calculateLive2DPlacement({...args,viewTransform:next});
    expect((100-after.y)/after.scale).toBeCloseTo((100-before.y)/before.scale,3);
  });
});
