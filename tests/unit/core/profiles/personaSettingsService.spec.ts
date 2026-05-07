import { afterEach, describe, expect, it } from "vitest";
import { cleanupTestServices, createTestServices } from "../../../../tests/helpers/testServices";

afterEach(cleanupTestServices);

describe("PersonaSettingsService", () => {
  it("maps the active profile and soul into editable persona settings", () => {
    const services = createTestServices("aurabot-persona-settings-");
    const settings = services.personaSettingsService.getActivePersonaSettings();

    expect(settings).toMatchObject({
      name: "Aura",
      shortPersonaDescription: expect.any(String),
      tone: expect.any(String),
      relationshipStyle: expect.any(String),
      proactiveLevel: "balanced",
      principles: expect.any(Array),
      taboos: expect.any(Array),
      reminderStyle: "",
      replyPreference: ""
    });
  });

  it("persists light and semi-pro persona fields back into profile and soul storage", () => {
    const services = createTestServices("aurabot-persona-settings-update-");

    const updated = services.personaSettingsService.updateActivePersonaSettings({
      name: "Auri",
      shortPersonaDescription: "会陪我聊天，也会更主动提醒我。",
      tone: "直接、放松、短句",
      relationshipStyle: "像熟悉的长期搭子",
      proactiveLevel: "high",
      principles: ["先接住用户情绪，再推进事情。"],
      taboos: ["不要像客服一样说话。"],
      reminderStyle: "像顺手提醒一句，不要太正式。",
      replyPreference: "多用短句，少用解释腔。"
    });

    expect(updated).toMatchObject({
      displayName: "Auri",
      soul: {
        name: "Auri",
        persona: "会陪我聊天，也会更主动提醒我。",
        tone: "直接、放松、短句",
        relationship: "像熟悉的长期搭子",
        proactiveLevel: "high",
        principles: ["先接住用户情绪，再推进事情。"],
        taboos: ["不要像客服一样说话。"],
        reminderStyle: "像顺手提醒一句，不要太正式。",
        replyPreference: "多用短句，少用解释腔。"
      }
    });

    const reloaded = services.profileService.getOrSeedActiveProfile();
    expect(reloaded).toMatchObject(updated);
  });
});
