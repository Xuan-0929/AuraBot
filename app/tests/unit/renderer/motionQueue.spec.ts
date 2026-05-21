import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, test } from "vitest";
import { useMotionQueueStore } from "../../../frontend/src/features/pet-vue/stores/motionQueue";

describe("useMotionQueueStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  test("starts the default idle motion as a persistent background motion when definitions load", () => {
    const store = useMotionQueueStore();

    store.setMotionDefinitions({
      Idle: [{ File: "idle.motion3.json" }],
      TapBody: [{ File: "tap.motion3.json" }]
    });

    expect(store.defaultIdleMotion).toEqual({ group: "Idle", index: 0 });
    expect(store.currentMotion).toEqual({ group: "Idle", index: 0 });
  });

  test("lets explicit motions interrupt idle and returns to idle when they finish", async () => {
    const store = useMotionQueueStore();
    store.setMotionDefinitions({
      Idle: [{ File: "idle.motion3.json" }],
      TapBody: [{ File: "tap.motion3.json" }]
    });

    await store.requestMotion("TapBody", 0);

    expect(store.currentMotion).toEqual({ group: "TapBody", index: 0 });
    expect(store.queued).toEqual([]);

    store.completeCurrentMotion();

    expect(store.currentMotion).toEqual({ group: "Idle", index: 0 });
  });

  test("does not backlog multiple explicit motions while one is already playing", async () => {
    const store = useMotionQueueStore();
    store.setMotionDefinitions({
      Idle: [{ File: "idle.motion3.json" }],
      TapBody: [{ File: "tap.motion3.json" }],
      Wave: [{ File: "wave.motion3.json" }]
    });

    expect(await store.requestMotion("TapBody", 0)).toBe(true);
    expect(await store.requestMotion("Wave", 0)).toBe(false);

    expect(store.currentMotion).toEqual({ group: "TapBody", index: 0 });
    expect(store.queued).toEqual([]);

    store.completeCurrentMotion();

    expect(store.currentMotion).toEqual({ group: "Idle", index: 0 });
  });
});
