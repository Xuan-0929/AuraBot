import { describe, expect, test } from "vitest";
import {
  createLive2DMotionSmoother,
  createMotionSmoothingUpdateHook
} from "../../../frontend/src/features/live2d/motionSmoothing";

describe("createLive2DMotionSmoother", () => {
  test("blends abrupt parameter jumps over multiple frames", () => {
    const model = createCoreModel([0]);
    const smoother = createLive2DMotionSmoother({ durationMs: 200 });

    smoother.captureCurrent(model);
    model.values[0] = 10;

    smoother.smoothTowardCurrent(model, 0.016);

    expect(model.values[0]).toBeGreaterThan(0);
    expect(model.values[0]).toBeLessThan(10);
  });

  test("can reset to the current pose without blending from stale motion values", () => {
    const model = createCoreModel([3]);
    const smoother = createLive2DMotionSmoother({ durationMs: 200 });

    smoother.captureCurrent(model);
    model.values[0] = 9;
    smoother.reset(model);
    smoother.smoothTowardCurrent(model, 0.016);

    expect(model.values[0]).toBe(9);
  });
});

describe("createMotionSmoothingUpdateHook", () => {
  test("runs the original motion update before smoothing its resulting pose", () => {
    const model = createCoreModel([0]);
    const hook = createMotionSmoothingUpdateHook({ durationMs: 200 });

    const updated = hook(model, 0.016, (coreModel) => {
      coreModel.setParameterValueByIndex(0, 10);
      return true;
    });

    expect(updated).toBe(true);
    expect(model.values[0]).toBeGreaterThan(0);
    expect(model.values[0]).toBeLessThan(10);
  });
});

function createCoreModel(values: number[]) {
  return {
    values,
    getParameterCount() {
      return values.length;
    },
    getParameterValueByIndex(index: number) {
      return values[index] ?? 0;
    },
    setParameterValueByIndex(index: number, value: number) {
      values[index] = value;
    }
  };
}
