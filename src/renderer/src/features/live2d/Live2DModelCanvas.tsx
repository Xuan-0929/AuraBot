import { useEffect, useRef, useState } from "react";
import type { DisplayObject } from "pixi.js";
import cubismCoreUrl from "live2dcubismcore/live2dcubismcore.min.js?url";
import type { AgentStatus } from "../../../../shared/contracts/chat";
import type { PetCursorPosition } from "../../../../shared/contracts/ipc";
import {
  calculateLive2DPlacement,
  resolveLive2DHostSize
} from "./live2dDiagnostics";
import { getLive2DMotionForAgentStatus } from "./live2dStatus";

const CUBISM_CORE_URL = cubismCoreUrl;
const LIVE2D_FALLBACK_HOST_SIZE = {
  width: 270,
  height: 380
};

interface Live2DModelCanvasProps {
  modelPath: string;
  agentStatus: AgentStatus;
  compact?: boolean;
  debug?: boolean;
  cursorPosition?: PetCursorPosition;
}

interface Live2DModelRuntime {
  autoUpdate: boolean;
  internalModel: {
    width: number;
    height: number;
  };
  anchor: {
    set(x: number, y: number): void;
  };
  scale: {
    set(scale: number): void;
  };
  x: number;
  y: number;
  motion(group: string, index?: number): unknown;
  focus(x: number, y: number, instant?: boolean): void;
  on(eventName: "hit", handler: () => void): void;
}

declare global {
  interface Window {
    PIXI?: unknown;
    Live2DCubismCore?: unknown;
  }
}

export function Live2DModelCanvas({
  modelPath,
  agentStatus,
  compact = false,
  debug = false,
  cursorPosition
}: Live2DModelCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const modelRef = useRef<Live2DModelRuntime | undefined>(undefined);
  const lastMotionSignatureRef = useRef<string | undefined>(undefined);
  const [renderState, setRenderState] = useState<"loading" | "ready" | "fallback">("loading");
  const activeMotion = getLive2DMotionForAgentStatus(agentStatus);

  useEffect(() => {
    let disposed = false;
    let pixiApp: { destroy(removeView?: boolean, stageOptions?: unknown): void } | undefined;
    const resizeFrameIds: number[] = [];
    const resizeTimeoutIds: number[] = [];

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
        const model = (await Live2DModel.from(modelPath, { autoInteract: false })) as Live2DModelRuntime;
        model.autoUpdate = true;

        if (disposed) {
          app.destroy(true);
          return;
        }

        pixiApp = app;
        modelRef.current = model;
        model.anchor.set(0.5, 0.95);
        model.x = host.clientWidth / 2;
        model.y = host.clientHeight;
        app.stage.addChild(model as unknown as DisplayObject);

        const resizeModel = () => {
          const hostSize = resolveLive2DHostSize(host, LIVE2D_FALLBACK_HOST_SIZE);
          const placement = calculateLive2DPlacement({
            hostSize,
            modelSize: model.internalModel,
            compact
          });

          app.renderer.resize(hostSize.width, hostSize.height);
          model.scale.set(placement.scale);
          model.x = placement.x;
          model.y = placement.y;
        };
        resizeModel();

        const resizeObserver = new ResizeObserver(resizeModel);
        resizeObserver.observe(host);
        resizeFrameIds.push(
          window.requestAnimationFrame(() => {
            resizeModel();
            resizeFrameIds.push(window.requestAnimationFrame(resizeModel));
          })
        );
        for (const delay of [80, 300, 1000]) {
          resizeTimeoutIds.push(window.setTimeout(resizeModel, delay));
        }
        model.on("hit", () => {
          model.motion("TapBody");
        });

        setRenderState("ready");

        return () => {
          resizeObserver.disconnect();
          for (const frameId of resizeFrameIds) {
            window.cancelAnimationFrame(frameId);
          }
          for (const timeoutId of resizeTimeoutIds) {
            window.clearTimeout(timeoutId);
          }
        };
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
      modelRef.current = undefined;
      pixiApp?.destroy(true, { children: true, texture: true, baseTexture: true });
    };
  }, [compact, modelPath]);

  useEffect(() => {
    const model = modelRef.current;

    if (!model || renderState !== "ready") {
      return;
    }

    const motion = getLive2DMotionForAgentStatus(agentStatus);
    const signature = `${motion.group}:${motion.index ?? "default"}`;

    if (lastMotionSignatureRef.current === signature) {
      return;
    }

    lastMotionSignatureRef.current = signature;
    model.motion(motion.group, motion.index);
  }, [agentStatus, renderState]);

  useEffect(() => {
    const model = modelRef.current;
    const host = hostRef.current;

    if (!model || !host || !cursorPosition || renderState !== "ready") {
      return;
    }

    const hostRect = host.getBoundingClientRect();

    model.focus(cursorPosition.localX - hostRect.left, cursorPosition.localY - hostRect.top);
  }, [cursorPosition, renderState]);

  return (
    <div
      className="live2d-canvas-host"
      data-render-state={renderState}
      data-motion-group={activeMotion.group}
      ref={hostRef}
    >
      <canvas className="live2d-canvas" ref={canvasRef} />
      {debug ? (
        <div className="live2d-debug-badge" aria-label="Live2D debug">
          {renderState} / {activeMotion.group}
        </div>
      ) : null}
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
