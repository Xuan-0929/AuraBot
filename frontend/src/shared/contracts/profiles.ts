/** Shares serializable Profile configuration, canonical SOUL text/fingerprints, saved/applied status and controlled editor requests.
 * Electron owns these files; Backend exclusively owns the Context/Memory paths exposed as status.
 */
import type { Profile } from '../schemas/profile';
import type { AvatarAdaptor, AvatarResource } from './avatar';
export interface ProfileConfig { profile: Profile; soulText: string; soulFingerprint: string; revision: number; adaptor: AvatarAdaptor; modelVersion: string }
export interface ProfileSummary {
  id: string; displayName: string; selected: boolean; revision: number; appliedRevision: number | null;
  modelName: string | null; visualStatus: 'unconfigured' | 'ready' | 'pending' | 'invalid';
}
export interface ProfileDetail {
  saved: ProfileConfig; applied: ProfileConfig | null; selected: boolean;
  memory: { directory: string; initialized: boolean };
  resources: AvatarResource[];
}
export interface ProfileSaveInput { profile: Profile; adaptor: AvatarAdaptor; soulText?: string; expectedSoulFingerprint?: string }
export interface ProfileDeleteGuard { allowed: boolean; reason?: string }
