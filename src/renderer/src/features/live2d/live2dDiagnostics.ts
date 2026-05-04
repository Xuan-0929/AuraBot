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
  compact
}: {
  hostSize: Live2DSize;
  modelSize: Live2DSize;
  compact: boolean;
}): Live2DPlacement {
  const widthScale = hostSize.width / Math.max(modelSize.width, 1);
  const heightScale = hostSize.height / Math.max(modelSize.height, 1);
  const scale = Math.min(widthScale, heightScale) * (compact ? 1.06 : 0.92);

  return {
    x: hostSize.width / 2,
    y: hostSize.height,
    scale
  };
}
