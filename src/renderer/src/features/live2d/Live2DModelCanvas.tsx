import { useEffect, useRef, useState } from "react";
import cubismCoreUrl from "live2dcubismcore/live2dcubismcore.min.js?url";

const CUBISM_CORE_URL = cubismCoreUrl;

interface Live2DModelCanvasProps {
  modelPath: string;
  compact?: boolean;
}

declare global {
  interface Window {
    PIXI?: unknown;
    Live2DCubismCore?: unknown;
  }
}

export function Live2DModelCanvas({ modelPath, compact = false }: Live2DModelCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [renderState, setRenderState] = useState<"loading" | "ready" | "fallback">("loading");

  useEffect(() => {
    let disposed = false;
    let pixiApp: { destroy(removeView?: boolean, stageOptions?: unknown): void } | undefined;

    async function mountModel() {
      const host = hostRef.current;
      const canvas = canvasRef.current;

      if (!host || !canvas) {
        return;
      }

      try {
        await ensureScript(CUBISM_CORE_URL);
        const PIXI = await import("pixi.js");
        window.PIXI = PIXI;
        const { Live2DModel } = await import("pixi-live2d-display/cubism4");
        Live2DModel.registerTicker(PIXI.Ticker);
        const app = new PIXI.Application({
          view: canvas,
          backgroundAlpha: 0,
          antialias: true,
          preserveDrawingBuffer: true,
          resizeTo: host
        });
        const model = await Live2DModel.from(modelPath);
        model.autoUpdate = true;

        if (disposed) {
          app.destroy(true);
          return;
        }

        pixiApp = app;
        model.anchor.set(0.5, 0.95);
        model.x = host.clientWidth / 2;
        model.y = host.clientHeight;
        app.stage.addChild(model);

        const resizeModel = () => {
          app.renderer.resize(host.clientWidth, host.clientHeight);
          const widthScale = host.clientWidth / Math.max(model.internalModel.width, 1);
          const heightScale = host.clientHeight / Math.max(model.internalModel.height, 1);
          const scale = Math.min(widthScale, heightScale) * (compact ? 1.06 : 0.92);
          model.scale.set(scale);
          model.x = host.clientWidth / 2;
          model.y = host.clientHeight;
        };
        resizeModel();

        const resizeObserver = new ResizeObserver(resizeModel);
        resizeObserver.observe(host);
        model.on("hit", () => {
          model.motion("TapBody");
        });

        setRenderState("ready");

        return () => resizeObserver.disconnect();
      } catch (error) {
        console.warn("Live2D renderer fallback:", error);
        setRenderState("fallback");
      }
    }

    let cleanupResize: (() => void) | undefined;
    void mountModel().then((cleanup) => {
      cleanupResize = cleanup;
    });

    return () => {
      disposed = true;
      cleanupResize?.();
      pixiApp?.destroy(true, { children: true, texture: true, baseTexture: true });
    };
  }, [compact, modelPath]);

  return (
    <div className="live2d-canvas-host" data-render-state={renderState} ref={hostRef}>
      <canvas className="live2d-canvas" ref={canvasRef} />
      {renderState === "fallback" ? (
        <div className="live2d-fallback" aria-hidden="true">
          <div className="live2d-fallback-face">
            <span />
            <span />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ensureScript(src: string): Promise<void> {
  if (window.Live2DCubismCore) {
    return Promise.resolve();
  }

  const existing = document.querySelector<HTMLScriptElement>(`script[src="${src}"]`);
  if (existing) {
    return new Promise((resolve, reject) => {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => reject(new Error(`Failed to load ${src}`)), { once: true });
    });
  }

  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.append(script);
  });
}
