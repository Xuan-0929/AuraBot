// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PersonaSettingsPage } from "../../../src/renderer/src/features/settings/pages/PersonaSettingsPage";
import type { PersonaSettings } from "../../../src/shared/contracts/personaSettings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const personaSettings: PersonaSettings = {
  name: "Aura",
  shortPersonaDescription: "会陪我聊天，也会顺手帮我推进事情。",
  tone: "轻松、短句、自然",
  relationshipStyle: "像熟悉的长期搭子",
  proactiveLevel: "balanced",
  principles: ["先保证事实可靠。"],
  taboos: ["不要像客服一样说话。"],
  reminderStyle: "像顺手提醒一句",
  replyPreference: "少解释，多一点自然感"
};

describe("PersonaSettingsPage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  it("shows the light editor by default and hides advanced fields initially", async () => {
    await act(async () => {
      root.render(
        <PersonaSettingsPage
          petSettings={{ clickThroughPet: false, displayLanguage: "en" }}
          personaSettings={personaSettings}
          onUpdatePersonaSettings={vi.fn(async () => undefined)}
        />
      );
    });

    const englishTitle = container.querySelector<HTMLElement>(".settings-page-lead h2")?.textContent;
    const englishHint = container.querySelector<HTMLElement>(".settings-page-lead p")?.textContent;

    expect(englishTitle).toBe("Persona");
    expect(englishHint).toBeTruthy();
    expect(container.textContent).toContain("Name");
    expect(container.textContent).toContain("Tone");
    expect(container.textContent).toContain("Relationship");
    expect(container.textContent).toContain("Proactive level");
    expect(container.textContent).not.toContain("Principles");
    expect(container.textContent).not.toContain("Taboos");

    await act(async () => {
      root.render(
        <PersonaSettingsPage
          petSettings={{ clickThroughPet: false, displayLanguage: "zh" }}
          personaSettings={personaSettings}
          onUpdatePersonaSettings={vi.fn(async () => undefined)}
        />
      );
    });

    expect(container.querySelector<HTMLElement>(".settings-page-lead h2")?.textContent).toBe("人格");
    expect(container.querySelector<HTMLElement>(".settings-page-lead p")?.textContent).toBeTruthy();
    expect(container.querySelector<HTMLElement>(".settings-page-lead p")?.textContent).not.toBe(englishHint);
  });

  it("reveals semi-pro fields after entering advanced editing", async () => {
    await act(async () => {
      root.render(
        <PersonaSettingsPage
          petSettings={{ clickThroughPet: false, displayLanguage: "en" }}
          personaSettings={personaSettings}
          onUpdatePersonaSettings={vi.fn(async () => undefined)}
        />
      );
    });

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[aria-label="Open advanced persona editing"]')!.click();
    });

    expect(container.textContent).toContain("Principles");
    expect(container.textContent).toContain("Taboos");
    expect(container.textContent).toContain("Reminder style");
    expect(container.textContent).toContain("Reply preference");
  });
});
