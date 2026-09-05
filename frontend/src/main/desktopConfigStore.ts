/** Owns the one application JSON file and saved/applied snapshots.
 * Forms send redacted configuration with write-only secret replacements; saves are atomic and never
 * change live state. Backend and Pi consume saved configuration only during explicit application/start.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync, renameSync, chmodSync } from 'node:fs';
import { createDefaultProfile } from '../core/profiles/soulService';
import { createPersonaSettingsFromProfile } from '../shared/contracts/personaSettings';
import { join, isAbsolute } from 'node:path';
import { desktopConfigSchema, type DesktopConfig, type SettingsState } from '../shared/contracts/desktop';

const secretPaths = ['chat_model.api_key','task.model.api_key','main_agent.speech.api_key','main_agent.web.tavily_api_key'];
function access(config:DesktopConfig, path:string, value?:string):string {
  const parts=path.split('.'); let target:any=config.backend;
  for(const part of parts.slice(0,-1)) target=target[part];
  const key=parts.at(-1)!; if(value!==undefined) target[key]=value;
  return target[key];
}
export class DesktopConfigStore {
  readonly path:string;
  private saved:DesktopConfig;
  private applied?:DesktopConfig;
  online=false;
  error?:string;
  capabilities?:SettingsState["capabilities"];
  constructor(readonly root:string, projectRoot:string) {
    mkdirSync(root,{recursive:true}); this.path=join(root,'settings.json');
    const blankModel={base_url:'',model_name:'',api_key:'',timeout_seconds:60};
    const seed=createDefaultProfile();
    const defaults:DesktopConfig={version:1,revision:0,persona:createPersonaSettingsFromProfile({...seed.profile,soul:seed.soul}),backend:{
      app:{data_dir:root,workspaces:[{id:'default',path:projectRoot}]},chat_model:{...blankModel},
      main_agent:{profile_dir:'profiles',web:{tavily_api_key:''},speech:{enabled:false,api_key:'',stt_model_name:'fun-asr-realtime',tts_model_name:'cosyvoice-v3-flash',tts_voice:'longanyang',
        silero_model_path:join(projectRoot,'assets/models/silero-vad.onnx'),silero_license_path:join(projectRoot,'assets/licenses/silero-vad.txt'),
        smart_turn_model_path:join(projectRoot,'assets/models/smart-turn-v3.2.onnx'),smart_turn_license_path:join(projectRoot,'assets/licenses/smart-turn-v3.2.txt')}},
      memory:{embedding:{provider:'hash',dimension:384}},task:{user_data_dir:root,pi_runtime_dir:join(projectRoot,'pi-runtime'),node_executable:process.execPath.includes('Electron')?'node':process.execPath,default_timeout_seconds:1800,model:{...blankModel}}
    },desktop:{pet:{clickThroughPet:false,displayLanguage:'zh'},currentModelId:'hiyori',systemNotificationsEnabled:true}};
    this.saved=existsSync(this.path)?desktopConfigSchema.parse(JSON.parse(readFileSync(this.path,'utf8'))):defaults;
    this.persist();
  }
  snapshot():DesktopConfig { return structuredClone(this.saved); }
  current():DesktopConfig { return structuredClone(this.applied??this.saved); }
  publicConfig():DesktopConfig { const value=this.snapshot(); for(const path of secretPaths) access(value,path,''); return value; }
  status():SettingsState {
    return {config:this.publicConfig(),appliedRevision:this.applied?.revision??null,pending:JSON.stringify(this.saved)!==JSON.stringify(this.applied),
      backendPending:JSON.stringify(this.saved.backend)!==JSON.stringify(this.applied?.backend)||JSON.stringify(this.saved.persona)!==JSON.stringify(this.applied?.persona),online:this.online,capabilities:this.capabilities,error:this.error,
      secrets:Object.fromEntries(secretPaths.map(path=>[path,Boolean(access(this.saved,path))]))};
  }
  save(input:unknown, clearSecrets:string[]=[]):SettingsState {
    const next=desktopConfigSchema.parse(input);
    // Empty redacted fields preserve credentials; clearing requires an explicit named action.
    for(const path of secretPaths) if(!access(next,path)) access(next,path,clearSecrets.includes(path)?'':access(this.saved,path));
    // Storage and runtime installation locations are application-owned, never mutable from a renderer.
    next.backend.app.data_dir=this.saved.backend.app.data_dir;
    next.backend.task.user_data_dir=this.saved.backend.task.user_data_dir;
    next.backend.task.pi_runtime_dir=this.saved.backend.task.pi_runtime_dir;
    next.backend.main_agent.profile_dir=this.saved.backend.main_agent.profile_dir;
    for(const m of [next.backend.chat_model,next.backend.task.model]) {
      if(m.base_url && !/^https?:\/\//.test(m.base_url)) throw new Error('模型地址必须使用 HTTP 或 HTTPS');
    }
    const ids=new Set<string>();
    for(const workspace of next.backend.app.workspaces){
      if(!isAbsolute(workspace.path))throw new Error('工作目录必须使用绝对路径');
      if(ids.has(workspace.id))throw new Error('工作区名称不能重复');
      ids.add(workspace.id);
    }
    next.revision=this.saved.revision;
    if(JSON.stringify(next)!==JSON.stringify(this.saved)) next.revision++;
    const previous=this.saved; this.saved=next;
    try { this.persist(); } catch(error) { this.saved=previous; throw error; }
    return this.status();
  }
  markApplied(snapshot:DesktopConfig) { this.applied=structuredClone(snapshot); this.error=undefined; }
  private persist() {
    const temporary=this.path+'.tmp';
    writeFileSync(temporary,JSON.stringify(this.saved,null,2)+'\n',{mode:0o600});
    chmodSync(temporary,0o600); renameSync(temporary,this.path);
  }
}
