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
  viewTransform
}: {
  hostSize: Live2DSize;
  modelSize: Live2DSize;
  compact: boolean;
  viewTransform?: Partial<Live2DViewTransform>;
}): Live2DPlacement {
  const transform = normalizeLive2DViewTransform(viewTransform);
  const widthScale = hostSize.width / Math.max(modelSize.width, 1);
  const heightScale = hostSize.height / Math.max(modelSize.height, 1);
  const scale = Math.min(widthScale, heightScale) * (compact ? 1.06 : 0.92) * transform.scale;
  const scaledWidth = modelSize.width * scale;
  const scaledHeight = modelSize.height * scale;

  return {
    x: roundPlacementValue(hostSize.width / 2 + (DEFAULT_LIVE2D_ANCHOR.x - 0.5) * scaledWidth + transform.offset.x),
    y: roundPlacementValue(hostSize.height / 2 + (DEFAULT_LIVE2D_ANCHOR.y - 0.5) * scaledHeight + transform.offset.y),
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
