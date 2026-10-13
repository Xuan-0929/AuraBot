/** Describes legacy structured persona settings consumed by ProfileRepository during migration.
 * Current personas are stored as SOUL.md; these fields only preserve older saved edits.
 */
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
