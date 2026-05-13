import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

const repoRoot = join(import.meta.dirname, "../../..");

describe("Vue pet quick controls visibility", () => {
  test("keeps the three quick control buttons always visible instead of hover-gated", () => {
    const petAppSource = readFileSync(
      join(repoRoot, "src/renderer/src/features/pet-vue/PetApp.vue"),
      "utf8"
    );

    expect(petAppSource).toContain("<ControlButtons");
    expect(petAppSource).toContain(':visible="true"');
    expect(petAppSource).not.toContain(':visible="isHoverUiVisible"');
  });
});
