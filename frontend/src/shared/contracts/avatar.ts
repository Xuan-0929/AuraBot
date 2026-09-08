/** Defines the persisted Profile capability table and versioned SAY visual metadata.
 * Main validates assets and compiles capabilities; preview and Pet consume these same semantics
 * while retaining independent mutable playback state. Times ending in _ms are milliseconds.
 */
export type AvatarState = 'idle' | 'listening' | 'thinking' | 'speaking';
export interface AvatarEntry {
  kind: 'motion' | 'expression';
  source: { file: string };
  description: string;
  agent_enabled: boolean;
  playback: { mode: 'once' | 'loop' | 'hold'; fade_in_ms?: number; fade_out_ms?: number };
}
export interface AvatarAdaptor {
  schema_version: 1;
  revision: number;
  model: { manifest: string } | null;
  entries: Record<string, AvatarEntry>;
  /** Repository-derived SDK verification receipts; never accepted as proof from saved form JSON. */
  validated_resources?: string[];
  states: Record<AvatarState, string | null>;
}
export interface AvatarResource {
  file: string;
  kind: 'motion' | 'expression';
  referenced: boolean;
  verified: boolean;
  error?: string;
}
export interface AvatarInstruction {
  motion?: string;
  expression?: string;
}
export interface AvatarCue {
  profile_id: string;
  generation: number;
  config_revision: number;
  say_id: string;
  call_id: string;
  invoke_id: string;
  avatar?: AvatarInstruction;
}
export const AVATAR_LIMITS = Object.freeze({ entries: 128, agentEntries: 32, nameLength: 64, descriptionLength: 256, fadeMs: 5000 });
export function emptyAvatarAdaptor(): AvatarAdaptor {
  return { schema_version: 1, revision: 1, model: null, entries: {}, states: { idle: null, listening: null, thinking: null, speaking: null } };
}
/** Builds a scoped URL; paths keep the adaptor's stable model/ prefix and relative directories. */
export function avatarResourceUrl(profileId:string,modelVersion:string,file:string,instance?:string):string {
  const path=[profileId,modelVersion,...file.split('/')].map(encodeURIComponent).join('/');
  return `aurabot-model://profile/${path}${instance?'?instance='+encodeURIComponent(instance):''}`;
}
