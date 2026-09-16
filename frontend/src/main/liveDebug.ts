/** Owns opt-in live diagnostics outside the audio transport.
 * One non-overlapping HTTP poll merges Backend events with Pet IPC, fences capture/identity changes,
 * and publishes bounded UI deltas. A bounded serial file writer owns rotation, clearing and shutdown;
 * diagnostic network or disk failures never control the live conversation. */
import { randomUUID } from 'node:crypto';
import { mkdir, stat, appendFile, rename, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { appendDebugEvents, sanitizeDebugDetails, sanitizeDebugEvent, DEBUG_MAX_BYTES,
  type BackendDebugBatch, type DebugState, type LiveDebugCapture, type LiveDebugEvent, type LiveDebugState, type LiveDebugUpdate } from '../shared/contracts/liveDebug';

type Request=(path:string,init?:RequestInit)=>Promise<any>;
const API='/api/v1/main-agent/debug';
const FILE_BYTES=5*1024*1024;

export class LiveDebugService {
  readonly logDirectory:string;
  private enabled=false;
  private captureId=randomUUID();
  private readonly instanceId=randomUUID();
  private revision=0;
  private sequence=0;
  private events:LiveDebugEvent[]=[];
  private updates:LiveDebugEvent[]=[];
  private state:DebugState={};
  private dropped=0;
  private networkError?:string;
  private diskError?:string;
  private online=false;
  private onlineEpoch=0;
  private identity?:{profileId:string;generation:number;configRevision:number};
  private backendInstance='';
  private cursor=0;
  private backendDropped=0;
  private configured=false;
  private closed=false;
  private transitioning=false;
  private transition:Promise<unknown>=Promise.resolve();
  private poll?:Promise<void>;
  private timer?:ReturnType<typeof setTimeout>;
  private publishTimer?:ReturnType<typeof setTimeout>;
  private writer?:Promise<void>;
  private lines:string[]=[];
  private lineBytes=0;
  private fileBytes?:number;
  private petSequences=new Map<string,number>();

  constructor(root:string,private request:Request,private changed:(value:LiveDebugUpdate)=>void,
    private captureChanged:(value:LiveDebugCapture)=>void) {this.logDirectory=join(root,'logs','live-debug');}

  capture():LiveDebugCapture {return {enabled:this.enabled,captureId:this.captureId};}
  snapshot():LiveDebugState {
    return {...this.capture(),revision:this.revision,events:[...this.events],state:structuredClone(this.state),
      dropped:this.dropped,error:this.diskError??this.networkError,logDirectory:this.logDirectory,updatedAt:Date.now()};
  }
  setIdentity(value:{profileId:string;generation:number;configRevision:number}|undefined):void {
    if(JSON.stringify(this.identity)===JSON.stringify(value))return;
    this.identity=value;
    this.state={system:{online:this.online},connection:{...value,online:this.online}};
    this.petSequences.clear();
    if(this.enabled)this.local('session.changed',value??{state:'offline'});
  }
  setOnline(online:boolean):void {
    if(this.online===online||this.closed)return;
    this.online=online;this.onlineEpoch++;this.configured=false;
    this.state.system={...this.state.system,online};
    if(!online){clearTimeout(this.timer);this.timer=undefined;this.networkError=undefined;}
    if(this.enabled)this.local(online?'backend.online':'backend.offline',{online});
    this.schedule(0);
  }
  setEnabled(enabled:boolean):Promise<LiveDebugState> {
    return this.serialize(async()=>{
      if(this.enabled===enabled){
        clearTimeout(this.timer);this.timer=undefined;await this.poll;
        if(this.online&&!this.configured)await this.configure(false);return this.snapshot();
      }
      this.enabled=enabled;this.captureId=randomUUID();this.configured=false;this.petSequences.clear();
      if(!enabled)this.captureChanged(this.capture());
      clearTimeout(this.timer);this.timer=undefined;
      await this.poll;
      if(enabled){this.diskError=undefined;this.local('debug.enabled',{instanceId:this.instanceId});}
      if(this.online)await this.configure(false);
      if(!enabled)await this.flush();
      this.publish();return this.snapshot();
    });
  }
  clear():Promise<LiveDebugState> {
    return this.serialize(async()=>{
      this.captureId=randomUUID();this.configured=false;
      clearTimeout(this.timer);this.timer=undefined;
      this.lines=[];this.lineBytes=0;this.events=[];this.updates=[];this.dropped=0;this.petSequences.clear();
      await this.poll;
      await this.writer;
      // Only this module's three files are removable; no renderer-controlled path is accepted.
      try {
        for(const suffix of ['', '.1', '.2'])await rm(join(this.logDirectory,'live-debug.jsonl'+suffix),{force:true});
        this.fileBytes=0;this.diskError=undefined;
      }catch{this.diskError='debug_log_clear_failed';}
      if(this.online)await this.configure(true);
      this.publish(true);return this.snapshot();
    });
  }
  private serialize(action:()=>Promise<LiveDebugState>):Promise<LiveDebugState> {
    const result=this.transition.then(async()=>{
      this.transitioning=true;
      try{return await action();}finally{this.transitioning=false;this.captureChanged(this.capture());this.schedule(500);}
    });
    this.transition=result.catch(()=>undefined);return result;
  }
  recordPet(raw:unknown):void {
    if(!this.enabled||this.closed||this.transitioning)return;
    const event=sanitizeDebugEvent(raw);
    if(!event||event.source!=='pet'||event.captureId!==this.captureId||!this.matches(event))return;
    const key=event.instanceId;
    if(event.seq<=(this.petSequences.get(key)??-1))return;
    this.petSequences.set(key,event.seq);
    if(this.petSequences.size>4)this.petSequences.delete(this.petSequences.keys().next().value!);
    this.state[event.stage]={...this.state[event.stage],...event.details,updatedAt:event.timestamp};
    this.accept(event);
  }
  private matches(event:Pick<LiveDebugEvent,'profileId'|'generation'|'configRevision'>):boolean {
    if(!event.profileId)return event.generation===undefined;
    return !this.identity || (event.profileId===this.identity.profileId
      && (event.generation===undefined||event.generation===this.identity.generation)
      && (event.configRevision===undefined||event.configRevision===this.identity.configRevision));
  }
  private accept(event:LiveDebugEvent):void {
    event=sanitizeDebugEvent({...event,id:++this.sequence})!;
    const count=this.events.length+1;
    this.events=appendDebugEvents(this.events,[event]);this.dropped+=count-this.events.length;
    this.updates=appendDebugEvents(this.updates,[event]);
    if(!this.diskError){
      const line=JSON.stringify(event)+'\n',size=Buffer.byteLength(line);
      while(this.lineBytes+size>DEBUG_MAX_BYTES&&this.lines.length){this.lineBytes-=Buffer.byteLength(this.lines.shift()!);this.dropped++;}
      this.lines.push(line);this.lineBytes+=size;this.startWriter();
    }
    if(!this.publishTimer)this.publishTimer=setTimeout(()=>this.publish(),500);
  }
  private local(name:string,details:LiveDebugEvent['details'],level:LiveDebugEvent['level']='info'):void {
    if(!this.enabled)return;
    this.accept({source:'main',seq:this.sequence+1,instanceId:this.instanceId,captureId:this.captureId,
      timestamp:Date.now(),stage:'system',name,level,...this.identity,details});
  }
  private publish(reset=false):void {
    clearTimeout(this.publishTimer);this.publishTimer=undefined;
    this.revision++;
    const value=this.snapshot();value.events=this.updates;this.updates=[];
    try{this.changed({...value,reset});}catch{/* Closing a debug window cannot affect collection. */}
  }
  private schedule(delay:number):void {
    // A failed disable retries only the control request, never reads events or starts capture.
    if(this.closed||(!this.enabled&&this.configured)||!this.online||this.transitioning||this.poll||this.timer)return;
    this.timer=setTimeout(()=>{
      this.timer=undefined;
      this.poll=this.readBackend().finally(()=>{this.poll=undefined;this.schedule(500);});
    },delay);
  }
  private async configure(clear:boolean):Promise<void> {
    const capture=this.captureId,epoch=this.onlineEpoch;
    try{
      const result=await this.request(API+(clear?'/clear':'/configure'),{method:'POST',
        body:JSON.stringify(clear?{captureId:capture}:{enabled:this.enabled,captureId:capture}),signal:AbortSignal.timeout(2000)});
      if(capture!==this.captureId||epoch!==this.onlineEpoch||!this.online||this.closed)return;
      if(typeof result.instanceId!=='string'||result.captureId!==capture||!Number.isSafeInteger(result.cursor))throw Error('Invalid debug response');
      if(clear&&result.enabled!==this.enabled){await this.configure(false);return;}
      if(result.enabled!==this.enabled)throw Error('Debug control not acknowledged');
      this.configured=true;this.backendInstance=result.instanceId;this.cursor=result.cursor;this.backendDropped=result.dropped??0;
      if(this.enabled)this.mergeState(result.state);this.networkError=undefined;
    }catch{if(capture===this.captureId&&epoch===this.onlineEpoch){this.configured=false;this.networkError='debug_backend_unavailable';}}
  }
  private async readBackend():Promise<void> {
    const capture=this.captureId,epoch=this.onlineEpoch;
    try{
      if(!this.configured)await this.configure(false);
      if(!this.enabled||!this.online||epoch!==this.onlineEpoch||capture!==this.captureId||!this.configured)return;
      const query=new URLSearchParams({after:String(this.cursor),instanceId:this.backendInstance,captureId:capture});
      const batch:BackendDebugBatch=await this.request(API+'/events?'+query,{signal:AbortSignal.timeout(2000)});
      if(this.closed||!this.online||epoch!==this.onlineEpoch||!this.enabled||capture!==this.captureId)return;
      if(batch.instanceId!==this.backendInstance){this.configured=false;this.local('backend.restarted',{});return;}
      if(batch.captureId!==capture||!batch.enabled){this.configured=false;return;}
      if(!Array.isArray(batch.events)||!Number.isSafeInteger(batch.cursor))throw Error('Invalid debug response');
      for(const raw of batch.events){const event=sanitizeDebugEvent(raw);if(event&&event.source==='backend'&&event.captureId===capture&&event.instanceId===this.backendInstance&&event.seq>this.cursor&&this.matches(event))this.accept(event);}
      this.cursor=batch.cursor;
      if(Number.isSafeInteger(batch.dropped)&&batch.dropped>this.backendDropped){const count=batch.dropped-this.backendDropped;this.backendDropped=batch.dropped;this.dropped+=count;this.local('events.dropped',{count},'warning');}
      this.mergeState(batch.state);this.networkError=undefined;this.publish();
    }catch{if(capture===this.captureId&&!this.closed){this.networkError='debug_backend_unavailable';this.publish();}}
  }
  private mergeState(raw:unknown):void {
    if(!raw||typeof raw!=='object'||Array.isArray(raw))return;
    for(const [stage,values] of Object.entries(raw)){
      if(!['connection','input','stt','eou','model','tts','playback','system'].includes(stage))continue;
      const details=sanitizeDebugDetails(values);
      if(!this.matches({profileId:typeof details.profileId==='string'?details.profileId:undefined,
        generation:typeof details.generation==='number'?details.generation:undefined,
        configRevision:typeof details.configRevision==='number'?details.configRevision:undefined}))continue;
      const bounded=sanitizeDebugEvent({source:'backend',seq:0,instanceId:'snapshot',captureId:this.captureId,timestamp:Date.now(),stage,name:'snapshot',level:'info',details});
      if(bounded)this.state[stage]=bounded.details;
    }
  }
  private startWriter():void {
    if(this.writer||!this.lines.length||this.diskError)return;
    this.writer=this.writePending().catch(()=>{
      this.diskError='debug_log_write_failed';this.lines=[];this.lineBytes=0;this.publish();
    }).finally(()=>{this.writer=undefined;if(this.lines.length)this.startWriter();});
  }
  private async writePending():Promise<void> {
    await mkdir(this.logDirectory,{recursive:true,mode:0o700});
    if(this.fileBytes===undefined){try{this.fileBytes=(await stat(join(this.logDirectory,'live-debug.jsonl'))).size;}catch{this.fileBytes=0;}}
    while(this.lines.length){
      const lines=this.lines;this.lines=[];this.lineBytes=0;
      let block='';
      for(const line of lines){
        if(this.fileBytes!+Buffer.byteLength(block)+Buffer.byteLength(line)>FILE_BYTES){
          if(block){await appendFile(join(this.logDirectory,'live-debug.jsonl'),block,{mode:0o600});this.fileBytes!+=Buffer.byteLength(block);block='';}
          await rm(join(this.logDirectory,'live-debug.jsonl.2'),{force:true});
          for(const [from,to] of [['.1','.2'],['','.1']]){
            try{await rename(join(this.logDirectory,'live-debug.jsonl'+from),join(this.logDirectory,'live-debug.jsonl'+to));}
            catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}
          }
          this.fileBytes=0;
        }
        block+=line;
      }
      if(block){await appendFile(join(this.logDirectory,'live-debug.jsonl'),block,{mode:0o600});this.fileBytes!+=Buffer.byteLength(block);}
    }
  }
  async flush():Promise<void> {this.startWriter();while(this.writer)await this.writer;}
  async shutdown():Promise<void> {
    await this.transition;await this.setEnabled(false);this.closed=true;
    clearTimeout(this.timer);clearTimeout(this.publishTimer);await this.poll;await this.flush();
  }
}
