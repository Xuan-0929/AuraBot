import { z } from "zod";

export const DEFAULT_PET_SETTINGS = {
  clickThroughPet: false,
  displayLanguage: "en"
} as const;

const displayLanguageSchema = z.enum(["en", "zh"]);

export const petSettingsSchema = z.object({
  clickThroughPet: z.boolean().default(DEFAULT_PET_SETTINGS.clickThroughPet),
  displayLanguage: displayLanguageSchema.default(DEFAULT_PET_SETTINGS.displayLanguage)
});

export const petSettingsPatchSchema = z.object({
  clickThroughPet: z.boolean().optional(),
  displayLanguage: displayLanguageSchema.optional()
});

export type PetSettings = z.infer<typeof petSettingsSchema>;
export type PetSettingsPatch = z.infer<typeof petSettingsPatchSchema>;

export function parsePetSettings(input: unknown): PetSettings {
  const parsed = petSettingsSchema.safeParse(input);

  return parsed.success ? parsed.data : { ...DEFAULT_PET_SETTINGS };
}

export function parsePetSettingsPatch(input: unknown): PetSettingsPatch {
  const parsed = petSettingsPatchSchema.safeParse(input);

  return parsed.success ? parsed.data : {};
}
