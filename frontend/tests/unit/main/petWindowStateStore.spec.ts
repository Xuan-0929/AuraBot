import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, test } from "vitest";
import { PetWindowStateStore } from "../../../src/main/petWindowStateStore";

describe("PetWindowStateStore", () => {
  test("starts without saved bounds when no state file exists", () => {
    const userDataPath = mkdtempSync(join(tmpdir(), "aurabot-pet-window-state-"));

    try {
      const store = new PetWindowStateStore(userDataPath);

      expect(store.getBounds()).toBeUndefined();
    } finally {
      rmSync(userDataPath, { force: true, recursive: true });
    }
  });

  test("persists the latest pet window bounds", () => {
    const userDataPath = mkdtempSync(join(tmpdir(), "aurabot-pet-window-state-"));

    try {
      const store = new PetWindowStateStore(userDataPath);
      store.saveBounds({ x: 122, y: 88, width: 300, height: 430 });

      const reloadedStore = new PetWindowStateStore(userDataPath);

      expect(reloadedStore.getBounds()).toEqual({ x: 122, y: 88, width: 300, height: 430 });
    } finally {
      rmSync(userDataPath, { force: true, recursive: true });
    }
  });

  test("ignores invalid legacy bounds files", () => {
    const userDataPath = mkdtempSync(join(tmpdir(), "aurabot-pet-window-state-"));

    try {
      mkdirSync(userDataPath, { recursive: true });
      writeFileSync(join(userDataPath, "pet-window-state.json"), JSON.stringify({ bounds: { x: "bad" } }), "utf8");

      const store = new PetWindowStateStore(userDataPath);

      expect(store.getBounds()).toBeUndefined();
    } finally {
      rmSync(userDataPath, { force: true, recursive: true });
    }
  });
});
