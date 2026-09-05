/** Defines desktop-owned configuration, delivered history and the read-only Task projection.
 * Main validates and persists these DTOs; renderer forms never own runtime configuration or secrets.
 */
import { z } from 'zod';
import type { Message } from '../schemas/message';
import { personaSettingsSchema } from '../schemas/personaSettings';
import { petSettingsSchema } from '../schemas/petSettings';

const model = z.object({ base_url:z.string().max(2048), model_name:z.string().max(256), api_key:z.string().max(8192), timeout_seconds:z.number().positive().max(600) });
export const desktopConfigSchema = z.object({
  version:z.literal(1), revision:z.number().int().nonnegative(), persona:personaSettingsSchema,
  backend:z.object({
    app:z.object({data_dir:z.string().min(1),workspaces:z.array(z.object({id:z.string().min(1).max(128),path:z.string().min(1)})).max(100)}),
    chat_model:model,
    main_agent:z.object({profile_dir:z.string().min(1),web:z.object({tavily_api_key:z.string().max(8192)}),speech:z.object({
      enabled:z.boolean(),api_key:z.string().max(8192),stt_model_name:z.string().min(1),tts_model_name:z.string().min(1),tts_voice:z.string().min(1),
      silero_model_path:z.string(),silero_license_path:z.string(),smart_turn_model_path:z.string(),smart_turn_license_path:z.string()
    })}),
    memory:z.object({embedding:z.object({provider:z.literal('hash'),dimension:z.literal(384)})}),
    task:z.object({user_data_dir:z.string(),pi_runtime_dir:z.string(),node_executable:z.string().min(1),default_timeout_seconds:z.number().int().min(1).max(86400),model:model})
  }),
  desktop:z.object({pet:petSettingsSchema,currentModelId:z.string().min(1),systemNotificationsEnabled:z.boolean()})
});
export type DesktopConfig = z.infer<typeof desktopConfigSchema>;
export interface SettingsState { config:DesktopConfig; appliedRevision:number|null; pending:boolean; backendPending:boolean; online:boolean; capabilities?:{text:boolean;voice:boolean;tasks:boolean}; error?:string; secrets:Record<string,boolean>; }
export interface BusyState { tasks:number; memoryWriting:boolean; mainAgent:boolean; }
export interface ApplyResult { state:SettingsState; busy?:BusyState; }
export interface HistoryEntry { id:string; role:'user'|'assistant'; content:string; }
export interface HistoryPage { messages:Message[]; nextCursor?:number; }
export interface TaskSummary { id:string; title:string; status:string; }
export interface PetCommand { id:string; type:'text'|'reconnect'|'disconnect'; text?:string; }
