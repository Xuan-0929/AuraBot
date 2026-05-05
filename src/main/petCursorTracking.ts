import type { PetCursorPosition } from "../shared/contracts/ipc";
import type { WindowRectangle } from "./windowPlacement";

export interface PetCursorPositionInput {
  cursor: {
    x: number;
    y: number;
  };
  petBounds: WindowRectangle;
}

export function createPetCursorPosition({ cursor, petBounds }: PetCursorPositionInput): PetCursorPosition {
  return {
    screenX: cursor.x,
    screenY: cursor.y,
    localX: cursor.x - petBounds.x,
    localY: cursor.y - petBounds.y
  };
}
