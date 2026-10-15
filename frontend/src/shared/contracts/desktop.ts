/** Defines desktop-owned configuration, delivered history and the read-only Task projection.
 * LaunchOptions is read-only process state, separate from persisted settings. ProfileRuntimeState exposes activation in progress to fence Pet media controls. Main validates and persists these DTOs; renderer forms never own runtime configuration or saved secrets. Legacy Tavily fields normalize into provider/api_key before validation; SettingsState carries masked
 * first/last-four previews separately from redacted configuration for editable key fields.
 * Legacy desktop speech fields remain readable for old settings files; active Profiles supply all conversation settings.
 * Speech settings migrate absent engine choices to Pipeline, strip legacy enabled flags, and keep SeedDuplex credentials independent; the Pet microphone owns session activation.
 * ProfileRuntimeState projects Realtime text-input ownership to every desktop window.
 */
import { z } from 'zod';
import type { Message } from '../schemas/message';
import { petSettingsSchema } from '../schemas/petSettings';

import {chatModelSchema as model, speechConfigSchema} from './voice';
const webSettings = z.preprocess((raw) => {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return raw;
  const web = raw as Record<string, unknown>;
  return { provider: Object.hasOwn(web, 'provider') ? web.provider : 'tavily', api_key: Object.hasOwn(web, 'api_key') ? web.api_key : (Object.hasOwn(web, 'tavily_api_key') ? web.tavily_api_key : '') };
}, z.object({ provider:z.literal('tavily'), api_key:z.string().max(8192) }));
export const desktopConfigSchema = z.object({
  version:z.literal(2), revision:z.number().int().nonnegative(),
  backend:z.object({
    app:z.object({data_dir:z.string().min(1),workspaces:z.array(z.object({id:z.string().min(1).max(128),path:z.string().min(1)})).max(100)}),
    chat_model:model,
    main_agent:z.object({profile_dir:z.string().min(1),web:webSettings,speech:speechConfigSchema}),
    memory:z.object({embedding:z.object({provider:z.literal('hash'),dimension:z.literal(384)})}),
    task:z.object({user_data_dir:z.string(),pi_runtime_dir:z.string(),node_executable:z.string().min(1),default_timeout_seconds:z.number().int().min(1).max(86400),model:model})
  }),
  desktop:z.object({pet:petSettingsSchema})
});
export type DesktopConfig = z.infer<typeof desktopConfigSchema>;
export interface LaunchOptions { readonly debug:boolean; readonly lockMemory:boolean; }
export interface SettingsState { launchOptions:LaunchOptions; config:DesktopConfig; appliedRevision:number|null; pending:boolean; backendPending:boolean; online:boolean; capabilities?:{text:boolean;voice:boolean;tasks:boolean}; error?:string; secrets:Record<string,boolean>; secretPreviews?:Record<string,string>; }
export interface BusyState { tasks:number; memoryWriting:boolean; mainAgent:boolean; }
export interface ApplyResult { state:SettingsState; busy?:BusyState; }
export interface HistoryEntry { profileId?:string; generation?:number; id:string; role:'user'|'assistant'; content:string; }
export interface HistoryPage { messages:Message[]; nextCursor?:number; }
export interface TaskSummary { id:string; title:string; status:string; }
export interface PetCommand { id:string; type:'text'|'reconnect'|'disconnect'; text?:string; }

export interface ProfileRuntimeState { textInputBlocked?:boolean; activating?:boolean; selectedId:string; activeId:string|null; revision:number|null; generation:number; online:boolean; error?:string; visualStatus?:'unconfigured'|'degraded'|'ready'|'unavailable'; visualError?:string; capabilityNames?:{motions:string[];expressions:string[]} }
export interface RealtimeToken { token:string; expiresInMs:number; subprotocol:string; profile_id:string; revision:number; generation:number }

export interface PetCommandResult { id:string; error?:string; visual?:{available:boolean;error?:string} }
