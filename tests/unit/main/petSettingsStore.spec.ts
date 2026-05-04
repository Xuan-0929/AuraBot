import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, test } from "vitest";
import { PetSettingsStore } from "../../../src/main/petSettingsStore";

describe("PetSettingsStore", () => {
  test("loads conservative desktop-pet defaults when no settings file exists", () => {
    const userDataPath = mkdtempSync(join(tmpdir(), "aurabot-pet-settings-"));

    try {
      const store = new PetSettingsStore(userDataPath);

      expect(store.get()).toEqual({
        clickThroughPet: false
      });
    } finally {
      rmSync(userDataPath, { force: true, recursive: true });
    }
  });

  test("merges and persists pet interaction settings", () => {
    const userDataPath = mkdtempSync(join(tmpdir(), "aurabot-pet-settings-"));

    try {
      const store = new PetSettingsStore(userDataPath);

      expect(store.update({ clickThroughPet: true })).toEqual({
        clickThroughPet: true
      });

      const reloadedStore = new PetSettingsStore(userDataPath);

      expect(reloadedStore.get()).toEqual({
        clickThroughPet: true
      });
    } finally {
      rmSync(userDataPath, { force: true, recursive: true });
    }
  });

  test("ignores legacy hide-on-hover settings from older builds", () => {
    const userDataPath = mkdtempSync(join(tmpdir(), "aurabot-pet-settings-"));

    try {
      mkdirSync(userDataPath, { recursive: true });
      writeFileSync(join(userDataPath, "pet-settings.json"), JSON.stringify({ hideOnHover: true }), "utf8");

      const store = new PetSettingsStore(userDataPath);

      expect(store.get()).toEqual({
        clickThroughPet: false
      });
    } finally {
      rmSync(userDataPath, { force: true, recursive: true });
    }
  });
});
