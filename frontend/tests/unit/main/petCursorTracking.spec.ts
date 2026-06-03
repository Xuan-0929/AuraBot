import { describe, expect, test } from "vitest";
import { createPetCursorPosition } from "../../../src/main/petCursorTracking";

describe("createPetCursorPosition", () => {
  test("keeps the system cursor point and derives pet-window local coordinates", () => {
    expect(
      createPetCursorPosition({
        cursor: { x: 1260, y: 520 },
        petBounds: { x: 900, y: 320, width: 300, height: 430 }
      })
    ).toEqual({
      screenX: 1260,
      screenY: 520,
      localX: 360,
      localY: 200
    });
  });

  test("keeps off-window local coordinates so Live2D can still look toward the cursor", () => {
    expect(
      createPetCursorPosition({
        cursor: { x: 780, y: 260 },
        petBounds: { x: 900, y: 320, width: 300, height: 430 }
      })
    ).toMatchObject({
      localX: -120,
      localY: -60
    });
  });
});
