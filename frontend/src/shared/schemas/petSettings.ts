/** Defines the pet preferences validated by desktopConfigSchema and shared with renderer stores.
 * Chinese is the first-run default; persistence and application belong to Electron Main. */
import { z } from "zod";

export const DEFAULT_PET_SETTINGS = {
  clickThroughPet: false,
  displayLanguage: "zh"
} as const;

const displayLanguageSchema = z.enum(["en", "zh", "ja"]);

export const petSettingsSchema = z.object({
  clickThroughPet: z.boolean().default(DEFAULT_PET_SETTINGS.clickThroughPet),
  displayLanguage: displayLanguageSchema.default(DEFAULT_PET_SETTINGS.displayLanguage)
});

export type PetSettings = z.infer<typeof petSettingsSchema>;
