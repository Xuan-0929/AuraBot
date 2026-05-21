import type { PersonaSettings } from "../../../shared/src/contracts/personaSettings";
import { createPersonaSettingsFromProfile } from "../../../shared/src/contracts/personaSettings";
import type { Profile } from "../../../shared/src/schemas/profile";
import type { ProfileRepository } from "../storage/repositories/profileRepository";
import type { ProfileService } from "./profileService";

export class PersonaSettingsService {
  constructor(
    private readonly profileService: ProfileService,
    private readonly profileRepository: ProfileRepository
  ) {}

  getActivePersonaSettings(): PersonaSettings {
    return createPersonaSettingsFromProfile(this.profileService.getOrSeedActiveProfile());
  }

  updateActivePersonaSettings(settings: PersonaSettings): Profile {
    const profile = this.profileService.getOrSeedActiveProfile();
    const updated = this.profileRepository.updateActivePersonaSettings(profile.id, settings, new Date().toISOString());

    return updated ?? profile;
  }
}
