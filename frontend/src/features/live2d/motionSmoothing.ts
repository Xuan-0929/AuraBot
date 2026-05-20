export interface Live2DCoreModelLike {
  getParameterCount(): number;
  getParameterValueByIndex(index: number): number;
  setParameterValueByIndex(index: number, value: number): void;
}

export interface Live2DMotionSmoother {
  captureCurrent(model: Live2DCoreModelLike): void;
  reset(model: Live2DCoreModelLike): void;
  smoothTowardCurrent(model: Live2DCoreModelLike, deltaSeconds: number): void;
}

export interface MotionSmoothingOptions {
  durationMs?: number;
}

const DEFAULT_SMOOTHING_DURATION_MS = 220;

export function createLive2DMotionSmoother(options: MotionSmoothingOptions = {}): Live2DMotionSmoother {
  const durationMs = Math.max(16, options.durationMs ?? DEFAULT_SMOOTHING_DURATION_MS);
  let previousValues: number[] | undefined;

  const captureCurrent = (model: Live2DCoreModelLike) => {
    previousValues = readParameterValues(model);
  };

  return {
    captureCurrent,
    reset(model) {
      captureCurrent(model);
    },
    smoothTowardCurrent(model, deltaSeconds) {
      if (!previousValues || previousValues.length !== model.getParameterCount()) {
        captureCurrent(model);
        return;
      }

      const alpha = calculateSmoothingAlpha(deltaSeconds, durationMs);
      const targetValues = readParameterValues(model);

      for (let i = 0; i < targetValues.length; i += 1) {
        const previous = previousValues[i] ?? targetValues[i]!;
        const target = targetValues[i]!;
        const blended = previous + (target - previous) * alpha;
        model.setParameterValueByIndex(i, blended);
        previousValues[i] = blended;
      }
    }
  };
}

export function createMotionSmoothingUpdateHook(options: MotionSmoothingOptions = {}) {
  const smoother = createLive2DMotionSmoother(options);

  return (
    model: Live2DCoreModelLike,
    deltaSeconds: number,
    hookedUpdate: (model: Live2DCoreModelLike, now: number) => boolean,
    now = deltaSeconds
  ): boolean => {
    smoother.captureCurrent(model);
    const result = hookedUpdate(model, now);
    smoother.smoothTowardCurrent(model, deltaSeconds);
    return result;
  };
}

function readParameterValues(model: Live2DCoreModelLike): number[] {
  const count = model.getParameterCount();
  const values: number[] = [];
  for (let i = 0; i < count; i += 1) {
    values.push(model.getParameterValueByIndex(i));
  }
  return values;
}

function calculateSmoothingAlpha(deltaSeconds: number, durationMs: number): number {
  if (!Number.isFinite(deltaSeconds) || deltaSeconds <= 0) {
    return 1;
  }
  const durationSeconds = durationMs / 1000;
  return Math.min(1, Math.max(0, 1 - Math.exp(-deltaSeconds / durationSeconds)));
}
