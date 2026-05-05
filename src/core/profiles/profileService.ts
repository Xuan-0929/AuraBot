import type { Profile } from "../../shared/schemas/profile";
import type { ProfileRepository } from "../storage/repositories/profileRepository";
import { createDefaultProfile, DEFAULT_PROFILE_ID } from "./soulService";

export class ProfileService {
  constructor(private readonly profileRepository: ProfileRepository) {}

  getOrSeedActiveProfile(): Profile {
    const existing = this.profileRepository.getActiveProfile();

    if (existing) {
      if (existing.id === DEFAULT_PROFILE_ID && existing.soul?.id === "soul_default") {
        const { profile, soul } = createDefaultProfile(new Date().toISOString());
        this.profileRepository.upsertDefaultProfile(
          {
            ...profile,
            createdAt: existing.createdAt
          },
          {
            ...soul,
            createdAt: existing.soul.createdAt
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
