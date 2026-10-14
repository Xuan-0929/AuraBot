/** Computes avatar placement zoom and persists renderer view transforms per stable Profile ID.
 * Focused zoom uses the same full-model/portrait framing as placement.
 * Existing default-profile layout is read once as a legacy fallback; other Profiles never inherit it.
 */
import {
  calculateLive2DPlacement,
  clampLive2DViewScale,
  normalizeLive2DViewTransform,
  type Live2DSize,
  type Live2DViewTransform
} from "./live2dDiagnostics";

export const LIVE2D_VIEW_TRANSFORM_STORAGE_KEY = "aurabot:live2d:view-transform";
export const LIVE2D_VIEW_SCALE_STORAGE_KEY = "aurabot:live2d:view-scale";

export function calculateNextLive2DViewScale(currentScale: number, wheelDeltaY: number): number {
  const delta = -wheelDeltaY * 0.001;

  return clampLive2DViewScale(currentScale * (1 + delta));
}

export function calculateFocusedLive2DViewTransform({
  hostSize,
  modelSize,
  compact,
  current,
  focalPoint,
  wheelDeltaY,
  showFullModel = true
}: {
  hostSize: Live2DSize;
  modelSize: Live2DSize;
  compact: boolean;
  current: Live2DViewTransform;
  focalPoint: { x: number; y: number };
  wheelDeltaY: number;
  showFullModel?: boolean;
}): Live2DViewTransform {
  const normalizedCurrent = normalizeLive2DViewTransform(current);
  const nextScale = calculateNextLive2DViewScale(normalizedCurrent.scale, wheelDeltaY);
  const currentPlacement = calculateLive2DPlacement({
    hostSize,
    modelSize,
    compact,
    showFullModel,
    viewTransform: normalizedCurrent
  });
  const nextBasePlacement = calculateLive2DPlacement({
    hostSize,
    modelSize,
    compact,
    showFullModel,
    viewTransform: { scale: nextScale, offset: { x: 0, y: 0 } }
  });
  const modelPoint = {
    x: (focalPoint.x - currentPlacement.x) / currentPlacement.scale,
    y: (focalPoint.y - currentPlacement.y) / currentPlacement.scale
  };
  const nextAnchor = {
    x: focalPoint.x - modelPoint.x * nextBasePlacement.scale,
    y: focalPoint.y - modelPoint.y * nextBasePlacement.scale
  };

  return {
    scale: nextScale,
    offset: {
      x: nextAnchor.x - nextBasePlacement.x,
      y: nextAnchor.y - nextBasePlacement.y
    }
  };
}

export function readStoredLive2DViewTransform(storage: Pick<Storage, "getItem">, profileId?:string): Live2DViewTransform {
  const key=profileId?`${LIVE2D_VIEW_TRANSFORM_STORAGE_KEY}:${profileId}`:LIVE2D_VIEW_TRANSFORM_STORAGE_KEY;
  const rawTransform = storage.getItem(key)??(profileId==='profile_default'?storage.getItem(LIVE2D_VIEW_TRANSFORM_STORAGE_KEY):null);
  if (rawTransform) {
    try {
      return normalizeLive2DViewTransform(JSON.parse(rawTransform));
    } catch {
      /* fall through to legacy scale */
    }
  }

  const rawScale = !profileId||profileId==='profile_default'?storage.getItem(LIVE2D_VIEW_SCALE_STORAGE_KEY):null;
  const legacyScale = rawScale ? Number.parseFloat(rawScale) : 1;

  return normalizeLive2DViewTransform({ scale: legacyScale });
}

export function writeStoredLive2DViewTransform(storage: Pick<Storage, "setItem">, value: Live2DViewTransform, profileId?:string): void {
  storage.setItem(profileId?`${LIVE2D_VIEW_TRANSFORM_STORAGE_KEY}:${profileId}`:LIVE2D_VIEW_TRANSFORM_STORAGE_KEY, JSON.stringify(normalizeLive2DViewTransform(value)));
}
