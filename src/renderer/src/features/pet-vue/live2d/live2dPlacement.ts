/**
 * Vue pet Live2D layout — reuses the same fit/center rules as the React
 * `Live2DModelCanvas` (`live2dDiagnostics.ts`).
 */
export {
  calculateLive2DPlacement,
  resolveLive2DHostSize,
  type Live2DHostLike,
  type Live2DPlacement,
  type Live2DSize
} from "../../live2d/live2dDiagnostics";

/** Matches `LIVE2D_FALLBACK_HOST_SIZE` in `Live2DModelCanvas.tsx`. */
export const PET_LIVE2D_FALLBACK_SIZE = { width: 270, height: 380 };
