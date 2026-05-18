import { clampLive2DViewScale } from "./live2dDiagnostics";

export const LIVE2D_VIEW_SCALE_STORAGE_KEY = "aurabot:live2d:view-scale";

export function calculateNextLive2DViewScale(currentScale: number, wheelDeltaY: number): number {
  const delta = -wheelDeltaY * 0.001;

  return clampLive2DViewScale(currentScale * (1 + delta));
}

export function readStoredLive2DViewScale(storage: Pick<Storage, "getItem">): number {
  const raw = storage.getItem(LIVE2D_VIEW_SCALE_STORAGE_KEY);
  const parsed = raw ? Number.parseFloat(raw) : 1;

  return clampLive2DViewScale(parsed);
}

export function writeStoredLive2DViewScale(storage: Pick<Storage, "setItem">, value: number): void {
  storage.setItem(LIVE2D_VIEW_SCALE_STORAGE_KEY, clampLive2DViewScale(value).toFixed(4));
}
