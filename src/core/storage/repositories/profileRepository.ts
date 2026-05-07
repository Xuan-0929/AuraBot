import type { PersonaSettings } from "../../../shared/contracts/personaSettings";
import type { Profile, Soul } from "../../../shared/schemas/profile";
import type { AppDatabase } from "../db";

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

export class ProfileRepository {
  constructor(private readonly database: AppDatabase) {}

  getActiveProfile(): Profile | undefined {
    const row = this.database
      .prepare(
        `
        select
          profiles.id,
          profiles.display_name,
          profiles.active,
          profiles.created_at,
          profiles.updated_at,
          souls.id as soul_id,
          souls.name as soul_name,
          souls.persona,
          souls.tone,
          souls.proactive_level,
          souls.principles_json,
          souls.taboos_json,
          souls.reminder_style,
          souls.reply_preference,
          souls.speaking_style,
          souls.relationship,
          souls.boundaries_json,
          souls.topic_preferences_json,
          souls.created_at as soul_created_at,
          souls.updated_at as soul_updated_at
        from profiles
        left join souls on souls.profile_id = profiles.id
        where profiles.active = 1
        limit 1
      `
      )
      .get() as ProfileRow | undefined;

    return row ? mapProfile(row) : undefined;
  }

  upsertDefaultProfile(profile: Profile, soul: Soul): void {
    const write = this.database.transaction(() => {
      this.database
        .prepare(
          `
          insert into profiles (id, display_name, active, created_at, updated_at)
          values (@id, @displayName, @active, @createdAt, @updatedAt)
          on conflict(id) do update set
            display_name = excluded.display_name,
            active = excluded.active,
            updated_at = excluded.updated_at
        `
        )
        .run({
          ...profile,
          active: profile.active ? 1 : 0
        });

      this.database
        .prepare(
          `
          insert into souls (
            id,
            profile_id,
            name,
            persona,
            tone,
            proactive_level,
            principles_json,
            taboos_json,
            reminder_style,
            reply_preference,
            speaking_style,
            relationship,
            boundaries_json,
            topic_preferences_json,
            created_at,
            updated_at
          )
          values (
            @id,
            @profileId,
            @name,
            @persona,
            @tone,
            @proactiveLevel,
            @principlesJson,
            @taboosJson,
            @reminderStyle,
            @replyPreference,
            @speakingStyle,
            @relationship,
            @boundariesJson,
            @topicPreferencesJson,
            @createdAt,
            @updatedAt
          )
          on conflict(profile_id) do update set
            name = excluded.name,
            persona = excluded.persona,
            tone = excluded.tone,
            proactive_level = excluded.proactive_level,
            principles_json = excluded.principles_json,
            taboos_json = excluded.taboos_json,
            reminder_style = excluded.reminder_style,
            reply_preference = excluded.reply_preference,
            speaking_style = excluded.speaking_style,
            relationship = excluded.relationship,
            boundaries_json = excluded.boundaries_json,
            topic_preferences_json = excluded.topic_preferences_json,
            updated_at = excluded.updated_at
        `
        )
        .run({
          ...soul,
          proactiveLevel: soul.proactiveLevel,
          principlesJson: JSON.stringify(soul.principles),
          taboosJson: JSON.stringify(soul.taboos),
          reminderStyle: soul.reminderStyle,
          replyPreference: soul.replyPreference,
          boundariesJson: JSON.stringify(soul.boundaries),
          topicPreferencesJson: JSON.stringify(soul.topicPreferences)
        });
    });

    write();
  }

  updateActivePersonaSettings(profileId: string, settings: PersonaSettings, now: string): Profile | undefined {
    const write = this.database.transaction(() => {
      this.database
        .prepare(
          `
          update profiles
          set display_name = ?, updated_at = ?
          where id = ?
        `
        )
        .run(settings.name, now, profileId);

      this.database
        .prepare(
          `
          update souls
          set
            name = ?,
            persona = ?,
            tone = ?,
            proactive_level = ?,
            principles_json = ?,
            taboos_json = ?,
            reminder_style = ?,
            reply_preference = ?,
            relationship = ?,
            updated_at = ?
          where profile_id = ?
        `
        )
        .run(
          settings.name,
          settings.shortPersonaDescription,
          settings.tone,
          settings.proactiveLevel,
          JSON.stringify(settings.principles),
          JSON.stringify(settings.taboos),
          settings.reminderStyle,
          settings.replyPreference,
          settings.relationshipStyle,
          now,
          profileId
        );
      return this.getActiveProfile();
    });

    return write();
  }
}

function mapProfile(row: ProfileRow): Profile {
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
