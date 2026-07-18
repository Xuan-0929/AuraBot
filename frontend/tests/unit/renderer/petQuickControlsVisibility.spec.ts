import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";

const rendererRoot = join(dirname(fileURLToPath(import.meta.url)), "../../../src");

describe("Vue pet quick controls visibility", () => {
  function readPetAppSource() {
    return readFileSync(join(rendererRoot, "features/pet-vue/PetApp.vue"), "utf8");
  }

  test("keeps the three quick control buttons always visible instead of hover-gated", () => {
    const petAppSource = readPetAppSource();

    expect(petAppSource).toContain("<ControlButtons");
    expect(petAppSource).toContain(':visible="true"');
    expect(petAppSource).not.toContain(':visible="isHoverUiVisible"');
  });

  test("keeps orange frame hover while avoiding model fade during resize interactions", () => {
    const petAppSource = readPetAppSource();

    expect(petAppSource).toMatch(/\.pet-vue-root:hover\s+\.pet-vue-model-shell\s*{[^}]*box-shadow:\s*inset 0 0 0 2px #f97316;/s);
    expect(petAppSource).toMatch(
      /\.pet-vue-root\[data-resizing="false"\]:hover:not\(:has\(\.pet-vue-resize-grip:hover\)\)\s+\.pet-vue-live2d-frame\s*{[^}]*opacity:\s*0\.4;/s
    );
    expect(petAppSource).not.toContain("pet-vue-model-hovering");
    expect(petAppSource).not.toContain(":class=\"{ 'pet-vue-model-hovering'");
  });
});
