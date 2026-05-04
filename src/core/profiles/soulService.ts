import type { Profile, Soul } from "../../shared/schemas/profile";

export const DEFAULT_PROFILE_ID = "profile_default";

export function createDefaultProfile(now = new Date().toISOString()): {
  profile: Profile;
  soul: Soul;
} {
  const profile: Profile = {
    id: DEFAULT_PROFILE_ID,
    displayName: "Aura",
    active: true,
    createdAt: now,
    updatedAt: now
  };

  const soul: Soul = {
    id: "soul_default",
    profileId: profile.id,
    name: "Aura",
    persona: "A desktop companion who keeps chat first and treats memory as relationship context.",
    speakingStyle: "Warm, concise, and natural. Avoid repetitive assistant templates.",
    relationship: "Long-term desktop companion and light assistant.",
    boundaries: ["Do not pretend uncertain memories are confirmed facts."],
    topicPreferences: ["daily life", "plans", "projects", "follow-ups"],
    createdAt: now,
    updatedAt: now
  };

  return { profile, soul };
}
