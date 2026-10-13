/** Owns the one application JSON file and saved/applied snapshots.
 * New installations use app-owned speech assets; existing paths remain authoritative. Forms send redacted configuration with write-only secret replacements; saves are atomic and never
 * change live state. Settings previews expose only the first/last four code points and length-matched
 * bullets; first-run defaults register the installation root and user home, and keep Pi dependencies
 * under that root independently of the source checkout. Launch options are immutable process state returned alongside settings, never persisted.
 * Legacy debug, notification and voice enable flags are stripped by the schema; microphone sessions control voice activation.
 * Legacy search credentials normalize on read; secrets never leave Main except masked previews.
 * Pipeline and Realtime credentials are retained separately across engine switches and redacted equally.
 */
import {speechAssetPaths} from './speechAssetManifest';
import { existsSync, mkdirSync, realpathSync, readFileSync, writeFileSync, renameSync, chmodSync } from 'node:fs';
import { join, isAbsolute, resolve } from 'node:path';
import { homedir } from 'node:os';
import { desktopConfigSchema, type DesktopConfig, type SettingsState, type LaunchOptions } from '../shared/contracts/desktop';

const secretPaths = ['chat_model.api_key','task.model.api_key','main_agent.speech.api_key','main_agent.speech.realtime.api_key','main_agent.web.api_key'];
function secretPreview(value:string):string {
  const characters=Array.from(value);
  return characters.length<=8 ? value : characters.slice(0,4).join('')+'•'.repeat(characters.length-8)+characters.slice(-4).join('');
}
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
  constructor(readonly root:string, readonly launchOptions:LaunchOptions=Object.freeze({debug:false,lockMemory:false})) {
    root=resolve(root);mkdirSync(root,{recursive:true});root=realpathSync(root);this.root=root;
    this.path=join(root,'settings.json');
    const blankModel={base_url:'',model_name:'',api_key:'',timeout_seconds:60};
    const defaults:DesktopConfig={version:2,revision:0,backend:{
      app:{data_dir:root,workspaces:[{id:'default',path:root},{id:'home',path:homedir()}]},chat_model:{...blankModel},
      main_agent:{profile_dir:'profiles',web:{provider:'tavily',api_key:''},speech:{api_key:'',stt_model_name:'fun-asr-realtime',tts_model_name:'cosyvoice-v3-flash',tts_voice:'longanyang',
        mode:'pipeline',realtime:{provider:'seeduplex',api_key:'',model:'1.2.6.1',voice:''},
        ...speechAssetPaths(root)}},
      memory:{embedding:{provider:'hash',dimension:384}},task:{user_data_dir:root,pi_runtime_dir:join(root,'pi-runtime'),node_executable:process.execPath.includes('Electron')?'node':process.execPath,default_timeout_seconds:1800,model:{...blankModel}}
    },desktop:{pet:{clickThroughPet:false,displayLanguage:'zh'}}};
    const existing=existsSync(this.path)?JSON.parse(readFileSync(this.path,'utf8')):null;
    this.saved=existing?desktopConfigSchema.parse({...existing,version:2}):defaults;
    // The repository must back up and import v1 persona before the old file is replaced.
    if(!existing)this.persist();
  }
  completeProfileMigration():void { this.persist(); }
  snapshot():DesktopConfig { return structuredClone(this.saved); }
  current():DesktopConfig { return structuredClone(this.applied??this.saved); }
  publicConfig():DesktopConfig { const value=this.snapshot(); for(const path of secretPaths) access(value,path,''); return value; }
  status():SettingsState {
    return {launchOptions:{...this.launchOptions},config:this.publicConfig(),appliedRevision:this.applied?.revision??null,pending:JSON.stringify(this.saved)!==JSON.stringify(this.applied),
      backendPending:JSON.stringify(this.saved.backend)!==JSON.stringify(this.applied?.backend),online:this.online,capabilities:this.capabilities,error:this.error,
      secretPreviews:Object.fromEntries(secretPaths.map(path=>[path,secretPreview(access(this.saved,path))])),
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
  markApplied(snapshot:DesktopConfig) { this.applied=structuredClone(snapshot);this.error=undefined; }
  private persist() {
    const temporary=this.path+'.tmp';
    writeFileSync(temporary,JSON.stringify(this.saved,null,2)+'\n',{mode:0o600});
    chmodSync(temporary,0o600); renameSync(temporary,this.path);
  }
}
