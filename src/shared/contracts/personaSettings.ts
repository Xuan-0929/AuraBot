import type { Profile } from "../schemas/profile";

export const PERSONA_PROACTIVE_LEVELS = ["low", "balanced", "high"] as const;

export type PersonaProactiveLevel = (typeof PERSONA_PROACTIVE_LEVELS)[number];

export interface PersonaSettings {
  name: string;
  shortPersonaDescription: string;
  tone: string;
  relationshipStyle: string;
  proactiveLevel: PersonaProactiveLevel;
  principles: string[];
  taboos: string[];
  reminderStyle: string;
  replyPreference: string;
}

export function createPersonaSettingsFromProfile(profile: Profile | undefined): PersonaSettings {
  const soul = profile?.soul;

  return {
    name: soul?.name ?? profile?.displayName ?? "Aura",
    shortPersonaDescription: soul?.persona ?? "",
    tone: soul?.tone ?? "",
    relationshipStyle: soul?.relationship ?? "",
    proactiveLevel: soul?.proactiveLevel ?? "balanced",
    principles: soul?.principles ?? [],
    taboos: soul?.taboos ?? [],
    reminderStyle: soul?.reminderStyle ?? "",
    replyPreference: soul?.replyPreference ?? ""
  };
}
