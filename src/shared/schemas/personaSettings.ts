import { z } from "zod";
import { PERSONA_PROACTIVE_LEVELS } from "../contracts/personaSettings";

export const personaSettingsSchema = z.object({
  name: z.string().min(1),
  shortPersonaDescription: z.string().min(1),
  tone: z.string().min(1),
  relationshipStyle: z.string().min(1),
  proactiveLevel: z.enum(PERSONA_PROACTIVE_LEVELS),
  principles: z.array(z.string().min(1)).default([]),
  taboos: z.array(z.string().min(1)).default([]),
  reminderStyle: z.string().default(""),
  replyPreference: z.string().default("")
});

export type PersonaSettingsInput = z.input<typeof personaSettingsSchema>;
