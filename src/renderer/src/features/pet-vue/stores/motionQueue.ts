import { defineStore } from "pinia";

export interface MotionRequest {
  group: string;
  index?: number;
}

interface MotionQueueState {
  queued: MotionRequest[];
  currentMotion?: MotionRequest;
  availableMotions: Map<string, Set<number | undefined>>;
}

export const useMotionQueueStore = defineStore("motionQueue", {
  state: (): MotionQueueState => ({
    queued: [],
    currentMotion: undefined,
    availableMotions: new Map()
  }),
  actions: {
    setMotionDefinitions(definitions: unknown) {
      this.availableMotions = parseMotionDefinitions(definitions);
      this.queued = this.queued.filter((motion) => hasMotion(this.availableMotions, motion.group, motion.index));
      if (this.currentMotion && !hasMotion(this.availableMotions, this.currentMotion.group, this.currentMotion.index)) {
        this.currentMotion = undefined;
      }
      this.promoteNextMotion();
    },
    async requestMotion(group: string, index?: number): Promise<boolean> {
      const normalized: MotionRequest = {
        group: group.trim(),
        index: typeof index === "number" ? index : undefined
      };

      if (!normalized.group || !hasMotion(this.availableMotions, normalized.group, normalized.index)) {
        return false;
      }

      // Skip when the request would re-trigger the motion that is already
      // playing or already sitting at the tail of the queue. This keeps the
      // Live2D motion manager's natural idle loop intact instead of restarting
      // it on every duplicate agent-status emission.
      if (matchesMotion(this.currentMotion, normalized)) {
        return false;
      }

      const lastQueued = this.queued[this.queued.length - 1];
      if (matchesMotion(lastQueued, normalized)) {
        return false;
      }

      this.queued.push(normalized);
      this.promoteNextMotion();
      return true;
    },
    completeCurrentMotion() {
      this.currentMotion = undefined;
      this.promoteNextMotion();
    },
    clearQueue() {
      this.queued = [];
      this.currentMotion = undefined;
    },
    promoteNextMotion() {
      if (this.currentMotion || this.queued.length === 0) {
        return;
      }
      this.currentMotion = this.queued.shift();
    }
  }
});

function matchesMotion(target: MotionRequest | undefined, candidate: MotionRequest): boolean {
  if (!target) {
    return false;
  }
  return target.group === candidate.group && target.index === candidate.index;
}

function hasMotion(definitions: Map<string, Set<number | undefined>>, group: string, index?: number): boolean {
  const groupDefinitions = definitions.get(group);
  if (!groupDefinitions || groupDefinitions.size === 0) {
    return false;
  }
  if (typeof index === "number") {
    return groupDefinitions.has(index);
  }
  return groupDefinitions.has(undefined) || groupDefinitions.size > 0;
}

function parseMotionDefinitions(input: unknown): Map<string, Set<number | undefined>> {
  const definitions = new Map<string, Set<number | undefined>>();
  if (!input || typeof input !== "object") {
    return definitions;
  }

  for (const [group, rawEntries] of Object.entries(input as Record<string, unknown>)) {
    const indices = new Set<number | undefined>();
    if (Array.isArray(rawEntries)) {
      rawEntries.forEach((entry, position) => {
        if (entry && typeof entry === "object" && typeof (entry as { Index?: unknown }).Index === "number") {
          indices.add((entry as { Index: number }).Index);
          return;
        }
        indices.add(position);
      });
    } else {
      indices.add(undefined);
    }

    definitions.set(group, indices);
  }

  return definitions;
}
