import { z } from "zod";

export const DEFAULT_PET_SETTINGS = {
  clickThroughPet: false
} as const;

export const petSettingsSchema = z.object({
  clickThroughPet: z.boolean().default(DEFAULT_PET_SETTINGS.clickThroughPet)
});

export const petSettingsPatchSchema = petSettingsSchema.partial();

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
