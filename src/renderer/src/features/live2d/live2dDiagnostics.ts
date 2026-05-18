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
  viewScale = 1
}: {
  hostSize: Live2DSize;
  modelSize: Live2DSize;
  compact: boolean;
  viewScale?: number;
}): Live2DPlacement {
  const widthScale = hostSize.width / Math.max(modelSize.width, 1);
  const heightScale = hostSize.height / Math.max(modelSize.height, 1);
  const scale = Math.min(widthScale, heightScale) * (compact ? 1.06 : 0.92) * clampLive2DViewScale(viewScale);
  const scaledWidth = modelSize.width * scale;
  const scaledHeight = modelSize.height * scale;

  return {
    x: roundPlacementValue(hostSize.width / 2 + (DEFAULT_LIVE2D_ANCHOR.x - 0.5) * scaledWidth),
    y: roundPlacementValue(hostSize.height / 2 + (DEFAULT_LIVE2D_ANCHOR.y - 0.5) * scaledHeight),
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

  return Math.min(Math.max(value, 0.01), 3);
}
