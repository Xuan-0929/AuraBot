/** Computes shared model placement and normalizes Profile-local view transforms for preview and Pet.
 * Full-model framing constrains the model canvas to the viewport; portrait framing fits its upper
 * 55 percent vertically and allows cropping. Both modes preserve the renderer's fixed anchor and
 * support the same pointer-focused zoom calculation without changing animation or GPU ownership.
 */
export interface Live2DSize {
  width: number;
  height: number;
}

export interface Live2DHostLike {
  clientWidth: number;
  clientHeight: number;
  getBoundingClientRect?: () => Pick<DOMRect, "width" | "height">;
}

export interface Live2DPlacement {
  x: number;
  y: number;
  scale: number;
}

export interface Live2DViewTransform {
  scale: number;
  offset: {
    x: number;
    y: number;
  };
}

const DEFAULT_LIVE2D_ANCHOR = {
  x: 0.5,
  y: 0.95
} as const;

export function resolveLive2DHostSize(host: Live2DHostLike, fallbackSize: Live2DSize): Live2DSize {
  const rect = host.getBoundingClientRect?.();
  const width = Math.round(host.clientWidth || rect?.width || fallbackSize.width);
  const height = Math.round(host.clientHeight || rect?.height || fallbackSize.height);

  return {
    width: Math.max(width, 1),
    height: Math.max(height, 1)
  };
}

export function calculateLive2DPlacement({
  hostSize,
  modelSize,
  compact,
  viewTransform,
  showFullModel = true
}: {
  hostSize: Live2DSize;
  modelSize: Live2DSize;
  compact: boolean;
  viewTransform?: Partial<Live2DViewTransform>;
  showFullModel?: boolean;
}): Live2DPlacement {
  const transform = normalizeLive2DViewTransform(viewTransform);
  const widthScale = hostSize.width / Math.max(modelSize.width, 1);
  const heightScale = hostSize.height / Math.max(modelSize.height, 1);
  const fitScale = Math.min(widthScale, heightScale);
  const requestedScale = (showFullModel ? fitScale * (compact ? 1.06 : 0.92) : heightScale * 0.92 / 0.55) * transform.scale;
  const scale = showFullModel ? Math.min(requestedScale, fitScale) : requestedScale;
  const scaledWidth = modelSize.width * scale;
  const scaledHeight = modelSize.height * scale;

  const x = hostSize.width / 2 + transform.offset.x;
  const y = (showFullModel
    ? hostSize.height / 2 + (DEFAULT_LIVE2D_ANCHOR.y - 0.5) * scaledHeight
    : hostSize.height * 0.04 + DEFAULT_LIVE2D_ANCHOR.y * scaledHeight) + transform.offset.y;
  const contain = (position:number, size:number, host:number, anchor:number) =>
    Math.max(anchor * size, Math.min(host - (1 - anchor) * size, position));
  return {
    x: roundPlacementValue(showFullModel ? contain(x, scaledWidth, hostSize.width, DEFAULT_LIVE2D_ANCHOR.x) : x),
    y: roundPlacementValue(showFullModel ? contain(y, scaledHeight, hostSize.height, DEFAULT_LIVE2D_ANCHOR.y) : y),
    scale
  };
}

function roundPlacementValue(value: number): number {
  return Number(value.toFixed(4));
}

export function clampLive2DViewScale(value: number): number {
  if (!Number.isFinite(value)) {
    return 1;
  }

  return Math.min(Math.max(value, 0.35), 2.4);
}

export function normalizeLive2DViewTransform(value: Partial<Live2DViewTransform> | undefined): Live2DViewTransform {
  return {
    scale: clampLive2DViewScale(value?.scale ?? 1),
    offset: {
      x: Number.isFinite(value?.offset?.x) ? value!.offset!.x : 0,
      y: Number.isFinite(value?.offset?.y) ? value!.offset!.y : 0
    }
  };
}
