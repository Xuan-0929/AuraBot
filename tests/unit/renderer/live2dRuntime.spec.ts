import { describe, expect, test, vi } from "vitest";
import {
  normalizeLive2DHitAreaNames,
  removeAndDestroyLive2DModel,
  type Live2DModelRuntime
} from "../../../src/renderer/src/features/live2d/runtime";

describe("normalizeLive2DHitAreaNames", () => {
  test("accepts the array shape returned by some Live2D models", () => {
    expect(normalizeLive2DHitAreaNames([{ name: "Body" }, { name: "" }, { name: "Head" }])).toEqual(["Body", "Head"]);
  });

  test("accepts object-map hit areas without relying on Array.map", () => {
    expect(
      normalizeLive2DHitAreaNames({
        Body: {},
        Face: { name: "Head" },
        Empty: { name: "" }
      })
    ).toEqual(["Body", "Head", "Empty"]);
  });

  test("returns an empty list for missing or invalid hit areas", () => {
    expect(normalizeLive2DHitAreaNames(undefined)).toEqual([]);
    expect(normalizeLive2DHitAreaNames("Body")).toEqual([]);
  });
});

describe("removeAndDestroyLive2DModel", () => {
  test("does not throw when the Pixi stage has already been destroyed", () => {
    const model = createModel();

    expect(() => removeAndDestroyLive2DModel({ stage: null } as never, model)).not.toThrow();
    expect(model.destroy).toHaveBeenCalledWith({ texture: true, baseTexture: true });
    expect(model.autoUpdate).toBe(false);
  });

  test("still destroys a model when stage removal rejects because it is no longer attached", () => {
    const model = createModel();
    const stage = {
      removeChild: vi.fn(() => {
        throw new Error("The supplied DisplayObject must be a child of the caller");
      })
    };

    expect(() => removeAndDestroyLive2DModel({ stage } as never, model)).not.toThrow();
    expect(stage.removeChild).toHaveBeenCalledWith(model);
    expect(model.destroy).toHaveBeenCalledWith({ texture: true, baseTexture: true });
  });
});

function createModel(): Live2DModelRuntime {
  return {
    autoUpdate: true,
    anchor: { set: vi.fn() },
    scale: { set: vi.fn() },
    x: 0,
    y: 0,
    width: 100,
    height: 100,
    motion: vi.fn(),
    focus: vi.fn(),
    hitTest: vi.fn(),
    destroy: vi.fn()
  };
}
