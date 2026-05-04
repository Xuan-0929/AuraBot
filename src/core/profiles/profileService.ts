import type { Profile } from "../../shared/schemas/profile";
import type { ProfileRepository } from "../storage/repositories/profileRepository";
import { createDefaultProfile } from "./soulService";

export class ProfileService {
  constructor(private readonly profileRepository: ProfileRepository) {}

  getOrSeedActiveProfile(): Profile {
    const existing = this.profileRepository.getActiveProfile();

    if (existing) {
      return existing;
    }

    const { profile, soul } = createDefaultProfile();
    this.profileRepository.upsertDefaultProfile(profile, soul);
    return { ...profile, soul };
  }
}
