export interface WindowRectangle {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface WindowSize {
  width: number;
  height: number;
}

export interface PlaceWindowNearPetInput {
  petBounds: WindowRectangle;
  targetSize: WindowSize;
  workArea: WindowRectangle;
  gap?: number;
}

export interface WindowPoint {
  x: number;
  y: number;
}

export interface ClampWindowBoundsInput {
  bounds: WindowRectangle;
  workArea: WindowRectangle;
  gap?: number;
}

const DEFAULT_GAP = 12;

export function placeWindowNearPet(input: PlaceWindowNearPetInput): WindowPoint {
  const gap = input.gap ?? DEFAULT_GAP;
  const y = clamp(
    input.petBounds.y,
    input.workArea.y + gap,
    input.workArea.y + input.workArea.height - input.targetSize.height - gap
  );
  const rightX = input.petBounds.x + input.petBounds.width + gap;

  if (rightX + input.targetSize.width <= input.workArea.x + input.workArea.width - gap) {
    return { x: rightX, y };
  }

  const leftX = input.petBounds.x - input.targetSize.width - gap;

  if (leftX >= input.workArea.x + gap) {
    return { x: leftX, y };
  }

  return {
    x: input.workArea.x + input.workArea.width - input.targetSize.width - gap,
    y: input.workArea.y + input.workArea.height - input.targetSize.height - gap
  };
}

export function clampWindowBoundsToWorkArea(input: ClampWindowBoundsInput): WindowRectangle {
  const gap = input.gap ?? DEFAULT_GAP;

  return {
    ...input.bounds,
    x: clamp(
      input.bounds.x,
      input.workArea.x + gap,
      input.workArea.x + input.workArea.width - input.bounds.width - gap
    ),
    y: clamp(
      input.bounds.y,
      input.workArea.y + gap,
      input.workArea.y + input.workArea.height - input.bounds.height - gap
    )
  };
}

function clamp(value: number, min: number, max: number): number {
  if (max < min) {
    return min;
  }

  return Math.min(Math.max(value, min), max);
}
