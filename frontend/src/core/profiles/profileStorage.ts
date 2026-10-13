/** Maps legacy SQLite identity/Soul rows for ProfileRepository's backed-up migration.
 * This module only decodes old records; ProfileRepository owns all current Profile persistence.
 */
import type { Profile, Soul } from "@shared/schemas/profile";

interface ProfileRow {
  id: string;
  display_name: string;
  active: number;
  created_at: string;
  updated_at: string;
  soul_id: string | null;
  soul_name: string | null;
  persona: string | null;
  tone: string | null;
  proactive_level: string | null;
  principles_json: string | null;
  taboos_json: string | null;
  reminder_style: string | null;
  reply_preference: string | null;
  speaking_style: string | null;
  relationship: string | null;
  boundaries_json: string | null;
  topic_preferences_json: string | null;
  soul_created_at: string | null;
  soul_updated_at: string | null;
}

export function mapProfile(row: ProfileRow): Profile {
  return {
    id: row.id,
    displayName: row.display_name,
    active: row.active === 1,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    soul: row.soul_id
      ? {
          id: row.soul_id,
          profileId: row.id,
          name: row.soul_name ?? "Aura",
          persona: row.persona ?? "",
          tone: row.tone || row.speaking_style || "",
          proactiveLevel: normalizeProactiveLevel(row.proactive_level),
          principles: JSON.parse(row.principles_json ?? "[]") as string[],
          taboos: JSON.parse(row.taboos_json ?? "[]") as string[],
          reminderStyle: row.reminder_style ?? "",
          replyPreference: row.reply_preference ?? "",
          speakingStyle: row.speaking_style ?? "",
          relationship: row.relationship ?? "",
          boundaries: JSON.parse(row.boundaries_json ?? "[]") as string[],
          topicPreferences: JSON.parse(row.topic_preferences_json ?? "[]") as string[],
          createdAt: row.soul_created_at ?? row.created_at,
          updatedAt: row.soul_updated_at ?? row.updated_at
        }
      : undefined
  };
}

function normalizeProactiveLevel(value: string | null): Soul["proactiveLevel"] {
  if (value === "low" || value === "high") {
    return value;
  }

  return "balanced";
}
