/** Validates Profile-owned conversation models and mutually exclusive speech engines.
 * Profile storage and the editor share these defaults; Main sends an applied snapshot to Backend.
 * Pipeline defaults target Volcengine; legacy vendor settings reset before secrets can be reused.
 * Credentials stay write-only across IPC, while both engine configurations survive mode changes.
 */
import {z} from 'zod';
export const chatModelSchema = z.object({ base_url:z.string().max(2048), model_name:z.string().max(256), api_key:z.string().max(8192), timeout_seconds:z.number().positive().max(600) });
const realtimeVoice = z.object({provider:z.literal('seeduplex'),api_key:z.string().max(8192),model:z.literal('1.2.6.1'),voice:z.string().max(256)});
export const speechConfigSchema = z.preprocess((input)=>{
 if(!input || typeof input!=='object')return input;
 const raw=input as Record<string,unknown>;
 if(/^(fun-asr|paraformer|qwen)/.test(String(raw.stt_model_name??'')) || /^(cosyvoice|sambert|qwen)/.test(String(raw.tts_model_name??'')))
  return {...raw,api_key:'',stt_model_name:'volc.seedasr.sauc.duration',tts_model_name:'seed-tts-2.0',tts_voice:'zh_female_vv_uranus_bigtts'};
 return input;
},z.object({
      mode:z.enum(['pipeline','realtime']).default('pipeline'),
      realtime:realtimeVoice.default({provider:'seeduplex',api_key:'',model:'1.2.6.1',voice:''}),
      api_key:z.string().max(8192),stt_model_name:z.string().min(1),tts_model_name:z.string().min(1),tts_voice:z.string().min(1),
      silero_model_path:z.string(),silero_license_path:z.string(),smart_turn_model_path:z.string(),smart_turn_license_path:z.string()
    }));
export const profileVoiceSchema=z.object({chat_model:chatModelSchema,speech:speechConfigSchema});
export type ProfileVoice=z.infer<typeof profileVoiceSchema>;
export function defaultProfileVoice():ProfileVoice {
 return {chat_model:{base_url:'',model_name:'',api_key:'',timeout_seconds:60},speech:{mode:'pipeline',realtime:{provider:'seeduplex',api_key:'',model:'1.2.6.1',voice:''},api_key:'',stt_model_name:'volc.seedasr.sauc.duration',tts_model_name:'seed-tts-2.0',tts_voice:'zh_female_vv_uranus_bigtts',silero_model_path:'assets/models/silero-vad.onnx',silero_license_path:'assets/licenses/silero-vad.txt',smart_turn_model_path:'assets/models/smart-turn-v3.2.onnx',smart_turn_license_path:'assets/licenses/smart-turn-v3.2.txt'}};
}
export const voiceSecretPaths=['chat_model.api_key','speech.api_key','speech.realtime.api_key'] as const;
export type VoiceSecretPath=typeof voiceSecretPaths[number];
export function voiceSecret(voice:ProfileVoice,path:VoiceSecretPath,value?:string):string {
 const target=path==='chat_model.api_key'?voice.chat_model:path==='speech.api_key'?voice.speech:voice.speech.realtime;
 if(value!==undefined)target.api_key=value;
 return target.api_key;
}
