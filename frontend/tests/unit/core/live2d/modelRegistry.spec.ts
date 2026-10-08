/** Verifies production model discovery against local manifest files.
 * Temporary model roots exercise the registry used by desktop model selection without Electron. */
import { mkdirSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, describe, expect, it } from "vitest";
import { createLive2DModelRegistry } from "@core/live2d/modelRegistry";

const tempDirs: string[] = [];

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("Live2DModelRegistry", () => {
  it("lists local model3 manifests from the configured model root", () => {
    const root = mkdtempSync(join(tmpdir(), "aurabot-live2d-models-"));
    tempDirs.push(root);

    mkdirSync(join(root, "alpha"), { recursive: true });
    writeFileSync(join(root, "alpha", "Alpha.model3.json"), "{}\n", "utf8");
    mkdirSync(join(root, "beta"), { recursive: true });
    writeFileSync(join(root, "beta", "Beta.model3.json"), "{}\n", "utf8");

    const registry = createLive2DModelRegistry({
      modelRootPath: root
    });
    const models = registry.listAvailableModels();

    expect(models.map((model) => model.id)).toEqual(["alpha", "beta"]);
    expect(models[0]).toMatchObject({
      displayName: "Alpha",
      manifestPath: "assets/live2d/alpha/Alpha.model3.json"
    });
  });
});
