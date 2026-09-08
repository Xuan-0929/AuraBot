/** Owns the development Python process from Electron startup through explicit Apply and app exit.
 * Only the child created here can be stopped. Readiness must match its instance/config revision, and a
 * busy restart must be approved by the user before Backend fences admission. No automatic restarts.
 */
import { randomUUID } from 'node:crypto';
import { spawn, type ChildProcess } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdirSync, writeFileSync, renameSync } from 'node:fs';
import { join } from 'node:path';
import type { DesktopConfigStore } from './desktopConfigStore';
import type { ApplyResult, DesktopConfig } from '../shared/contracts/desktop';
export const BACKEND_URL='http://127.0.0.1:6401';
export async function backendJson(path:string,init:RequestInit={}):Promise<any> {
  const response=await fetch(BACKEND_URL+path,{...init,headers:{Origin:'http://127.0.0.1:6400','Content-Type':'application/json',...init.headers},signal:init.signal??AbortSignal.timeout(5000)});
  if(!response.ok){let detail='';try{const body=await response.json();if(typeof body.detail==='string')detail=body.detail;}catch{/* Protocol failures do not expose response bodies. */}if(detail==='profile_cleanup_incomplete')throw new Error('旧 Profile 的资源尚未完成关闭；已暂停激活，请稍后重试，原数据保留。');throw new Error(`Backend 请求失败 (${response.status})${detail?': '+detail:''}`);}
  return response.json();
}
async function checkPort():Promise<void> {
  await new Promise<void>((resolve,reject)=>{const server=createServer();server.once('error',()=>reject(new Error('端口 6401 已被占用；未停止其他进程。')));server.listen(6401,'127.0.0.1',()=>server.close(()=>resolve()));});
}
export class BackendProcess {
  private child?:ChildProcess;
  private operation:Promise<unknown>|null=null;
  private shuttingDown=false;
  get busy():boolean {return this.operation!==null||this.shuttingDown;}
  private stopping=false;
  constructor(private config:DesktopConfigStore,private projectRoot:string,private changed:()=>void,private beforeStop:()=>void=()=>{}) {}
  start():Promise<void> {return this.exclusive(()=>this.startProcess());}
  private exclusive<T>(action:()=>Promise<T>):Promise<T> {
    if(this.busy)return Promise.reject(new Error('正在启动、退出或应用配置，请稍候。'));
    const operation=Promise.resolve().then(action);this.operation=operation;
    return operation.finally(()=>{if(this.operation===operation)this.operation=null;});
  }
  private async startProcess():Promise<void> {
    if(this.child) throw new Error('Backend 已经启动');
    await checkPort();
    const snapshot=this.config.snapshot(); this.preparePi(snapshot);const instanceId=randomUUID();
    const child=spawn('uv',['run','--project',join(this.projectRoot,'backend'),'uvicorn','backend.main:app','--host','127.0.0.1','--port','6401','--app-dir',this.projectRoot],{
      cwd:this.projectRoot,stdio:'ignore',env:{...process.env,AURABOT_CONFIG_PATH:this.config.path,AURABOT_BACKEND_INSTANCE:instanceId,AURABOT_TASK_API_KEY:snapshot.backend.task.model.api_key}
    });
    this.child=child;
    let spawnError=false;
    child.once('error',()=>{spawnError=true;if(!child.pid&&this.child===child)this.child=undefined;});
    child.once('exit',()=>{if(this.child===child)this.child=undefined;this.config.online=false;if(!this.stopping)this.config.error='Backend 已退出，请检查配置后重新应用。';this.changed();});
    try {
      const deadline=Date.now()+30000;
      while(Date.now()<deadline) {
        if(spawnError||child.exitCode!==null||child.signalCode!==null) throw new Error('Backend 启动失败，请检查 Python、uv 与配置。');
        try {
          const ready=await backendJson('/api/v1/readiness');
          if(ready.ready&&ready.instanceId===instanceId&&ready.configRevision===snapshot.revision){this.config.online=true;this.config.capabilities={text:ready.capabilities?.text===true,voice:ready.capabilities?.voice===true,tasks:ready.capabilities?.tasks===true};this.config.markApplied(snapshot);this.changed();return;}
        } catch { /* The child is still starting; bounded readiness retries only during startup. */ }
        await new Promise(resolve=>setTimeout(resolve,200));
      }
      throw new Error('Backend 启动超时，请查看运行依赖。');
    } catch(error) {await this.stopProcess();throw error;}
  }
  apply(force=false):Promise<ApplyResult> {return this.exclusive(()=>this.applySaved(force));}
  private async applySaved(force:boolean):Promise<ApplyResult> {
    try {
      if(this.config.online&&!this.config.status().backendPending){this.config.markApplied(this.config.snapshot());this.changed();return {state:this.config.status()};}
      if(this.child&&this.config.online) {
        const preflight=await backendJson('/api/v1/restart/prepare',{method:'POST',body:JSON.stringify({force})});
        if(!preflight.ready) return {state:this.config.status(),busy:preflight.busy};
      }
      await this.stopProcess(); await this.startProcess(); return {state:this.config.status()};
    } catch(error) {this.config.error=error instanceof Error?error.message:'配置应用失败';this.changed();throw error;}
  }
  async stop():Promise<void> {
    this.shuttingDown=true;
    try {await this.operation?.catch(()=>undefined);await this.stopProcess();}
    finally {this.shuttingDown=false;}
  }
  private async stopProcess():Promise<void> {
    const child=this.child;if(!child)return;
    this.beforeStop();
    this.stopping=true;
    try {
      await new Promise<void>((resolve,reject)=>{
        const timer=setTimeout(()=>{child.removeListener('exit',done);reject(new Error('Backend 尚未完成退出；未启动第二个进程。'));},40000);
        const done=()=>{clearTimeout(timer);resolve();};
        child.once('exit',done);child.kill('SIGTERM');
      });
    } finally {this.stopping=false;this.config.online=false;}
  }
  private preparePi(config:DesktopConfig) {
    const root=join(config.backend.task.user_data_dir,'pi-agent');mkdirSync(root,{recursive:true});
    const m=config.backend.task.model;
    const models={providers:{'aurabot-task':{baseUrl:m.base_url||'http://127.0.0.1:1',api:'openai-completions',apiKey:'$AURABOT_TASK_API_KEY',models:[{id:m.model_name||'unconfigured',name:m.model_name||'未配置',reasoning:false,input:['text'],contextWindow:128000,maxTokens:8192}]}}};
    for(const [name,value] of Object.entries({'models.json':models,'settings.json':{defaultProvider:'aurabot-task',defaultModel:m.model_name||'unconfigured'}})) {
      const path=join(root,name);writeFileSync(path+'.tmp',JSON.stringify(value,null,2),{mode:0o600});renameSync(path+'.tmp',path);
    }
  }
}
