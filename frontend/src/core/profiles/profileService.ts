import type { Profile } from "@shared/schemas/profile";
import type { ProfileRepository } from "../storage/repositories/profileRepository";
import { createDefaultProfile, DEFAULT_PROFILE_ID } from "./soulService";

export class ProfileService {
  constructor(private readonly profileRepository: ProfileRepository) {}

  getOrSeedActiveProfile(): Profile {
    const existing = this.profileRepository.getActiveProfile();

    if (existing) {
      if (shouldUpgradeDefaultProfile(existing)) {
        const { profile, soul } = createDefaultProfile(new Date().toISOString());
        this.profileRepository.upsertDefaultProfile(
          {
            ...profile,
            createdAt: existing.createdAt
          },
          {
            ...soul,
            createdAt: existing.soul?.createdAt ?? existing.createdAt
          }
        );

        return this.profileRepository.getActiveProfile() ?? existing;
      }

      return existing;
    }

    const { profile, soul } = createDefaultProfile();
    this.profileRepository.upsertDefaultProfile(profile, soul);
    return { ...profile, soul };
  }
}

function shouldUpgradeDefaultProfile(profile: Profile): boolean {
  const soul = profile.soul;

  return (
    profile.id === DEFAULT_PROFILE_ID &&
    soul?.id === "soul_default" &&
    (!!soul && (!soul.tone || soul.principles.length === 0 || soul.taboos.length === 0))
  );
}
