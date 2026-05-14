import type { Live2DHostLike, Live2DSize } from "./live2dDiagnostics";
import { resolveLive2DHostSize } from "./live2dDiagnostics";

export interface Live2DResizeController {
  resizeNow(): void;
  dispose(): void;
}

export function createLive2DResizeController(
  host: Live2DHostLike,
  fallbackSize: Live2DSize,
  onResize: (size: Live2DSize) => void
): Live2DResizeController {
  let frameId: number | undefined;
  let verifyFrameId: number | undefined;
  let lastWidth = 0;
  let lastHeight = 0;

  const emitIfChanged = () => {
    const size = resolveLive2DHostSize(host, fallbackSize);
    if (size.width !== lastWidth || size.height !== lastHeight) {
      lastWidth = size.width;
      lastHeight = size.height;
      onResize(size);
    }
    verifyFrameId = window.requestAnimationFrame(() => {
      verifyFrameId = undefined;
      const verified = resolveLive2DHostSize(host, fallbackSize);
      if (verified.width !== lastWidth || verified.height !== lastHeight) {
        lastWidth = verified.width;
        lastHeight = verified.height;
        onResize(verified);
      }
    });
  };

  const requestResize = () => {
    if (frameId !== undefined) {
      return;
    }
    frameId = window.requestAnimationFrame(() => {
      frameId = undefined;
      emitIfChanged();
    });
  };

  const resizeObserver = new ResizeObserver(requestResize);
  resizeObserver.observe(host as Element);
  window.addEventListener("resize", requestResize);

  return {
    resizeNow() {
      emitIfChanged();
    },
    dispose() {
      resizeObserver.disconnect();
      window.removeEventListener("resize", requestResize);
      if (frameId !== undefined) {
        window.cancelAnimationFrame(frameId);
      }
      if (verifyFrameId !== undefined) {
        window.cancelAnimationFrame(verifyFrameId);
      }
      frameId = undefined;
      verifyFrameId = undefined;
    }
  };
}
