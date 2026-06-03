import { describe, expect, test } from "vitest";
import { clampWindowBoundsToWorkArea, placeWindowNearPet } from "../../../src/main/windowPlacement";

describe("placeWindowNearPet", () => {
  test("places the console to the right of the pet when there is room", () => {
    expect(
      placeWindowNearPet({
        petBounds: { x: 80, y: 120, width: 300, height: 430 },
        targetSize: { width: 920, height: 700 },
        workArea: { x: 0, y: 0, width: 1600, height: 1000 },
        gap: 12
      })
    ).toEqual({ x: 392, y: 120 });
  });

  test("uses the left side when the pet is close to the right edge", () => {
    expect(
      placeWindowNearPet({
        petBounds: { x: 1260, y: 120, width: 300, height: 430 },
        targetSize: { width: 920, height: 700 },
        workArea: { x: 0, y: 0, width: 1600, height: 1000 },
        gap: 12
      })
    ).toEqual({ x: 328, y: 120 });
  });

  test("falls back to the bottom-right work area when neither side fits", () => {
    expect(
      placeWindowNearPet({
        petBounds: { x: 360, y: 420, width: 300, height: 430 },
        targetSize: { width: 920, height: 700 },
        workArea: { x: 0, y: 0, width: 1000, height: 760 },
        gap: 12
      })
    ).toEqual({ x: 68, y: 48 });
  });
});

describe("clampWindowBoundsToWorkArea", () => {
  test("keeps an in-bounds window where the user placed it", () => {
    expect(
      clampWindowBoundsToWorkArea({
        bounds: { x: 120, y: 88, width: 300, height: 430 },
        workArea: { x: 0, y: 0, width: 1600, height: 1000 },
        gap: 12
      })
    ).toEqual({ x: 120, y: 88, width: 300, height: 430 });
  });

  test("pulls a saved pet position back into the visible work area", () => {
    expect(
      clampWindowBoundsToWorkArea({
        bounds: { x: 1580, y: -120, width: 300, height: 430 },
        workArea: { x: 0, y: 0, width: 1600, height: 1000 },
        gap: 12
      })
    ).toEqual({ x: 1288, y: 12, width: 300, height: 430 });
  });
});
