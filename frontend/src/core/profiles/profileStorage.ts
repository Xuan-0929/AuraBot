/** Owns the desktop Profile database and its current profiles/souls schema.
 * Seeds one active identity, atomically saves persona fields and returns the same Soul projection
 * used by settings and Pet realtime; business state and migration history live outside this store.
 */
import type { PersonaSettings } from "@shared/contracts/personaSettings";
import type { Profile, Soul } from "@shared/schemas/profile";
import Database from 'better-sqlite3';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { createDefaultProfile } from './soulService';
import { createPersonaSettingsFromProfile } from '@shared/contracts/personaSettings';

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

export class ProfileStorage {
  private readonly database: Database.Database;
  constructor(filePath: string) {
    mkdirSync(dirname(filePath), { recursive: true });
    this.database = new Database(filePath);
    try {
      this.database.pragma('foreign_keys = ON');
      this.database.exec(`
        create table if not exists profiles (
          id text primary key, display_name text not null, active integer not null default 0,
          created_at text not null, updated_at text not null
        );
        create table if not exists souls (
          id text primary key, profile_id text not null unique references profiles(id) on delete cascade,
          name text not null, persona text not null, tone text not null,
          proactive_level text not null, principles_json text not null, taboos_json text not null,
          reminder_style text not null, reply_preference text not null, speaking_style text not null,
          relationship text not null, boundaries_json text not null, topic_preferences_json text not null,
          created_at text not null, updated_at text not null
        );
      `);
    } catch (error) { this.database.close(); throw error; }
  }

  close(): void { this.database.close(); }

  getOrSeedActiveProfile(): Profile {
    const existing = this.getActiveProfile();
    if (existing) return existing;
    const { profile, soul } = createDefaultProfile();
    this.upsertDefaultProfile(profile, soul);
    return { ...profile, soul };
  }

  getActivePersonaSettings(): PersonaSettings {
    return createPersonaSettingsFromProfile(this.getOrSeedActiveProfile());
  }

  private getActiveProfile(): Profile | undefined {
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

  private upsertDefaultProfile(profile: Profile, soul: Soul): void {
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

  updateActivePersonaSettings(settings: PersonaSettings): Profile {
    const profile = this.getOrSeedActiveProfile();
    const profileId = profile.id;
    const now = new Date().toISOString();
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
      return this.getActiveProfile() ?? profile;
    });

    return write();
  }
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
