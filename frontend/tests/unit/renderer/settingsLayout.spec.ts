import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";

const rendererRoot = join(dirname(fileURLToPath(import.meta.url)), "../../../src");
const settingsWindow = readFileSync(join(rendererRoot, "features/settings/SettingsWindow.vue"), "utf8");
const styles = readFileSync(join(rendererRoot, "styles.css"), "utf8");

describe("settings V2 layout", () => {
  test("uses a direct desktop sidebar and keeps the card navigation for compact layouts", () => {
    expect(settingsWindow).toContain("settings-v2-layout");
    expect(settingsWindow).toContain("settings-v2-sidebar");
    expect(settingsWindow).toContain(":aria-current=");
    expect(settingsWindow).toContain("isCompact");
    expect(settingsWindow).toContain('ref<SettingsPageKey>("general")');
    expect(settingsWindow).toContain("SettingsCenterShell");
  });

  test("animates only the desktop settings content when the selected page changes", () => {
    expect(settingsWindow).toContain('<Transition name="settings-page" mode="out-in">');
    expect(settingsWindow).toContain(':key="currentPage"');
    expect(styles).toContain(`.app-shell-settings .settings-page-enter-active {
  transition: opacity 160ms ease-out, transform 160ms ease-out;
}`);
    expect(styles).toContain(`.app-shell-settings .settings-page-enter-from {
  opacity: 0;
  transform: translateY(4px);
}`);
  });
});
