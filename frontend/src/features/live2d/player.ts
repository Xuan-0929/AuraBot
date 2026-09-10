/** Owns a resident Live2D instance's SDK sampling, composition and resource lifetime.
 * One Pixi ticker advances random resident rounds, body, expression, output-lip envelope and physics.
 * Loaded resident snapshots are adopted atomically; SAY temporarily owns the expression override. Preview and Pet create
 * separate players; only versioned delivery events reach PerformanceDirector. Framework's high-level
 * motion/Idle scheduler is never run, and curve evaluation stays in the pinned Cubism Framework.
 */
import type {Application,DisplayObject} from 'pixi.js';
import {UPDATE_PRIORITY} from 'pixi.js';
import type {AvatarAdaptor,AvatarState,AvatarCue,AvatarInstruction,AvatarEntry} from '@shared/contracts/avatar';
import {avatarResourceUrl,emptyAvatarAdaptor} from '@shared/contracts/avatar';
import {loadLive2DModel,removeAndDestroyLive2DModel,type Live2DModelRuntime} from './runtime';
import {CubismSampler,type SampledClip} from './cubismSampler';
import {PoseChannel,ease} from './poseChannel';
import {ResidentScheduler} from './residentScheduler';
import {PerformanceDirector,type PerformanceEvent} from './performance';
export interface PlayerConfig {modelPath:string;adaptor?:AvatarAdaptor;profileId?:string;revision?:number;modelVersion?:string;generation?:number;onPlaybackChange?:(name:string)=>void;}
export const avatarInstances={created:0,destroyed:0,active:0};
export class AvatarPlayer {
 readonly sampler:CubismSampler;
 readonly channel:PoseChannel;
 readonly diagnostics={updates:0,requests:0,starts:0,finishes:0,dropped:0,resourceErrors:0,residentDraws:0,residentStarts:0,residentFailures:0,residentStale:0};
 private director:PerformanceDirector;
 private generation:number;
 private clips=new Map<string,SampledClip>();
 private failedResources=new Set<string>();
 private pending=new Map<string,Promise<SampledClip>>();
 private abort=new AbortController();
 private disposed=false;
 private state:AvatarState='idle';
 private stateEpoch=0;
 private resident:ResidentScheduler;
 private residentExpression?:SampledClip;
 private residentClips=new Map<string,SampledClip>();
 private residentEntries:Record<string,AvatarEntry>={};
 private residentStates=emptyAvatarAdaptor().states;
 private idleBaseline?:SampledClip;
 private sayActive=false;
 private previewActive=false;
 private candidatePaused=false;
 private expressionOwnedIds=new Set<string>();
 private expressionSettling=false;
 private previewEpoch=0;
 private expression?:SampledClip;
 private expressionStart=0;
 private expressionFade=200;
 private expressionFrom:Float32Array;
 private expressionDelta:Float32Array;
 private expressionBase:Float32Array;
 private expressionTarget:Float32Array;
 private gazeWeight=0.35;
 private voiceOwned=false;
 private configFingerprint:string;
 private pinned=new Set<string>();
 private mouth=0;
 private mouthTarget=0;
 private wasGesture=false;
 adaptor:AvatarAdaptor;
 constructor(readonly app:Application,readonly model:Live2DModelRuntime,public config:PlayerConfig,sdk:any,private readonly onError:(message:string)=>void=()=>{},private readonly random:()=>number=Math.random){
  this.configFingerprint=JSON.stringify(config.adaptor??emptyAvatarAdaptor());this.adaptor=config.adaptor??emptyAvatarAdaptor();this.generation=config.generation??0;
  this.sampler=new CubismSampler(model.internalModel.coreModel,sdk);this.channel=new PoseChannel(this.sampler.rest);
  this.expressionFrom=this.sampler.rest.map(()=>0);this.expressionDelta=this.expressionFrom.slice();this.expressionBase=this.sampler.rest.slice();this.expressionTarget=this.sampler.rest.slice();
  this.resident=new ResidentScheduler(id=>this.startResident(id),random);
  this.director=this.makeDirector();model.autoUpdate=false;
  app.stage.addChild(model as unknown as DisplayObject);app.ticker.add(this.tick,this,UPDATE_PRIORITY.HIGH);
  avatarInstances.created++;avatarInstances.active++;
 }
 async configure(config:PlayerConfig){
  const fingerprint=JSON.stringify(config.adaptor??emptyAvatarAdaptor());
  if(this.config.revision===config.revision&&this.configFingerprint===fingerprint){this.setGeneration(config.generation??this.generation);return;}
  this.configFingerprint=fingerprint;this.stateEpoch++;this.failedResources.clear();
  this.director.clear();this.candidatePaused=false;this.syncResidentPause();this.config=config;this.adaptor=config.adaptor??emptyAvatarAdaptor();this.generation=config.generation??this.generation;this.director=this.makeDirector();
  await this.prepare();
 }
 private makeDirector(){return new PerformanceDirector({profile_id:this.config.profileId??'',generation:this.generation,config_revision:this.config.revision??0},{start:avatar=>{this.sayActive=true;this.syncResidentPause();this.perform(avatar);},finish:()=>{this.channel.cancel();this.sayActive=false;this.candidatePaused=false;this.syncResidentPause();this.setExpression(this.residentExpression,this.residentFade(this.residentExpression));},level:value=>{this.mouthTarget=value;},voice:owned=>{this.voiceOwned=owned;}});}
 setGeneration(generation:number){if(this.generation===generation)return;this.director.clear();this.candidatePaused=false;this.generation=generation;this.director=this.makeDirector();this.syncResidentPause();}
 accept(event:PerformanceEvent){if(this.disposed)return;try{if(event.type==='pause')this.candidatePaused=true;else if(event.type==='resume'||event.type==='reset')this.candidatePaused=false;this.syncResidentPause();this.director.accept(event);}catch{this.diagnostics.dropped++;}}
 private syncResidentPause(){this.resident.pause(this.sayActive||this.previewActive||this.candidatePaused);}
 async prepare(){
  const epoch=++this.stateEpoch;const adaptor=JSON.parse(JSON.stringify(this.adaptor)) as AvatarAdaptor;
  const names=[...Object.values(adaptor.states).flat(),...Object.entries(adaptor.entries).filter(([,entry])=>entry.agent_enabled).map(([name])=>name)];
  this.pinned=new Set(names.map(name=>{const entry=adaptor.entries[name];return entry?this.clipKey(entry.source.file,entry.kind,entry):'';}));
  const available=new Map<string,SampledClip>();
  for(const name of new Set(names)){
   if(this.disposed||epoch!==this.stateEpoch){this.diagnostics.residentStale++;return;}
   const entry=adaptor.entries[name];if(!entry||!adaptor.validated_resources?.includes(entry.source.file))continue;
   const key=this.clipKey(entry.source.file,entry.kind,entry);if(this.failedResources.has(key))continue;
   try{const clip=await this.load(entry.source.file,entry.kind,entry);available.set(name,clip);}catch(error){if(this.disposed||epoch!==this.stateEpoch){this.diagnostics.residentStale++;return;}this.failedResources.add(key);this.diagnostics.residentFailures++;this.report(error);}
  }
  if(this.disposed||epoch!==this.stateEpoch){this.diagnostics.residentStale++;return;}
  this.residentClips=available;this.residentEntries=adaptor.entries;this.residentStates=adaptor.states;
  // Scheduling compares resource identity as well as timing, without retaining SDK evaluators itself.
  const candidates=new Map([...available].map(([id,clip])=>{const entry=adaptor.entries[id]!;return [id,{identity:this.clipKey(entry.source.file,entry.kind,entry),kind:clip.kind,durationMs:clip.durationMs,fadeInMs:clip.fadeInMs,fadeOutMs:clip.fadeOutMs}] as const;}));
  this.resident.configure(adaptor.states,candidates);
  this.resident.setState(this.state);
 }
 private startResident(name:string|undefined){
  if(this.disposed)return;
  this.diagnostics.residentDraws++;
  const entry=name?this.residentEntries[name]:undefined;
  const clip=name?this.residentClips.get(name):undefined;
  if(clip){
   this.diagnostics.residentStarts++;
   if(clip.kind==='motion'){
    this.residentExpression=undefined;this.setExpression(undefined,this.residentFade());this.channel.setBaseline(clip,true);
    if(name&&this.residentStates.idle.includes(name))this.idleBaseline=clip;
   }else{
    const idle=this.residentStates.idle.map(id=>this.residentClips.get(id)).filter(c=>c?.kind==='motion').filter((c):c is SampledClip=>!!c);
    if(!this.idleBaseline||!idle.includes(this.idleBaseline))this.idleBaseline=idle[Math.min(idle.length-1,Math.floor(Math.max(0,this.random())*idle.length))];
    this.channel.setBaseline(this.idleBaseline);this.residentExpression=clip;this.setExpression(clip,this.residentFade(clip));
   }
  }else{this.residentExpression=undefined;this.channel.setBaseline(undefined);this.setExpression(undefined,this.residentFade());}
  const label=name&&this.adaptor.entries[name]?.source.file===entry?.source.file?this.adaptor.entries[name]?.display_name:entry?.display_name;
  this.config.onPlaybackChange?.(label||name||'');
 }
 private url(file:string){
  if(this.config.profileId&&this.config.modelVersion)return avatarResourceUrl(this.config.profileId,this.config.modelVersion,file);
  const manifest=this.adaptor.model?.manifest;const current=new URL(this.config.modelPath,location.href);
  if(manifest){const root=new URL('../'.repeat(manifest.split('/').length-1),current);return new URL(file,root).href;}
  return new URL(file,current).href;
 }
 async load(file:string,kind:'motion'|'expression',entry?:AvatarEntry):Promise<SampledClip>{
  const key=this.clipKey(file,kind,entry);
  const cached=this.clips.get(key);if(cached){this.clips.delete(key);this.clips.set(key,cached);return cached;}
  const existing=this.pending.get(key);if(existing)return existing;
  const promise=(async()=>{
   const response=await fetch(this.url(file),{signal:AbortSignal.any([this.abort.signal,AbortSignal.timeout(10000)])});
   if(!response.ok)throw new Error('动画资源加载失败');
   const text=await response.text();if(text.length>4*1024*1024)throw new Error('动画资源超过 4 MiB');
   const clip=this.sampler.create(JSON.parse(text),kind,entry?.playback);
   if(this.disposed)throw new Error('预览已关闭');
   // At most 64 configured resources plus a 16-item raw-preview LRU tail.
   if(this.clips.size>=80){const first=[...this.clips.keys()].find(key=>!this.pinned.has(key));if(first)this.clips.delete(first);}
   this.clips.set(key,clip);return clip;
  })();this.pending.set(key,promise);
  try{return await promise;}finally{this.pending.delete(key);}
 }
 private clipKey(file:string,kind:string,entry?:AvatarEntry){return `${kind}:${file}:${JSON.stringify(entry?.playback??{})}`;}
 private readyEntry(name:string|undefined,kind:'motion'|'expression'){
  if(!name)return;const entry=this.adaptor.entries[name];if(!entry||entry.kind!==kind)return;
  return this.clips.get(`${kind}:${entry.source.file}:${JSON.stringify(entry.playback)}`);
 }
 async setState(state:AvatarState){
  if(this.disposed)return;
  this.state=state;this.resident.setState(state);
  if(this.previewActive){this.previewEpoch++;this.channel.cancel();this.resident.restart();this.previewActive=false;this.syncResidentPause();}
 }
 perform(avatar:AvatarInstruction){
  if(avatar.motion&&avatar.motion!=='none'){
   this.diagnostics.requests++;const motion=this.readyEntry(avatar.motion,'motion');
   if(motion){this.channel.play(motion);this.diagnostics.starts++;}else this.diagnostics.dropped++;
  }
  if(avatar.expression==='neutral')this.setExpression(undefined);
  else if(avatar.expression){const expression=this.readyEntry(avatar.expression,'expression');if(expression)this.setExpression(expression);else this.diagnostics.dropped++;}
 }
 async play(input:{motion?:string;expression?:string;loop?:boolean}){
  const epoch=++this.previewEpoch;this.previewActive=true;this.syncResidentPause();
  try{
   const motion=input.motion?await this.load(input.motion,'motion'):undefined;
   const expression=input.expression?await this.load(input.expression,'expression'):undefined;
   if(this.disposed||epoch!==this.previewEpoch)return;
   if(motion){this.diagnostics.requests++;this.diagnostics.starts++;if(input.loop){this.channel.setBaseline(motion);}else this.channel.play(motion);}
   this.setExpression(expression);
  }catch(error){if(!this.disposed&&epoch===this.previewEpoch){this.previewActive=false;this.syncResidentPause();}throw error;}
 }
 stop(){this.previewEpoch++;this.previewActive=false;this.channel.cancel();this.state='idle';this.resident.pause(true);this.resident.setState('idle');this.resident.restart();this.syncResidentPause();this.setExpression(this.residentExpression,this.residentFade(this.residentExpression));}
 private residentFade(clip?:SampledClip){return Math.min(5000,Math.max(250,clip?.fadeInMs??0,this.expression?.fadeOutMs??0));}
 private setExpression(clip:SampledClip|undefined,fade?:number){
  if(this.expression===clip)return;
  this.expressionSettling=true;
  for(const id of this.expression?.ids??[])this.expressionOwnedIds.add(id);for(const id of clip?.ids??[])this.expressionOwnedIds.add(id);
  this.expressionFrom.set(this.expressionDelta);this.expressionFade=fade??(clip?.fadeInMs??this.expression?.fadeOutMs??200);this.expression=clip;this.expressionStart=this.channel.elapsedMs;
 }
 private tick=()=>this.advance(this.app.ticker.deltaMS);
 advance(deltaMs:number){
  if(this.disposed||!Number.isFinite(deltaMs)||deltaMs<=0)return;
  // Substeps share one clock; include resident boundaries exactly at every display refresh rate.
  let remaining=Math.min(deltaMs,100);
  while(remaining>1e-7){const step=Math.min(remaining,1000/120,this.resident.remaining);this.advanceStep(step);remaining-=step;this.resident.advance(step);}
 }
 private advanceStep(deltaMs:number){
  try{
   this.director.advance(deltaMs);
   const dt=this.channel.advance(deltaMs);if(!dt)return;
   const core=this.sampler.core;const internal=this.model.internalModel;
   this.sampler.write(this.channel.pose);
   const seconds=this.channel.elapsedMs/1000;
   // Native blinking runs before expression; expression masks remain authoritative after physics.
   internal.eyeBlink?.updateParameters(core,dt/1000);
   const bodyClips=[this.channel.currentBaseline,this.channel.currentGesture] as Array<SampledClip|undefined>;
   for(const id of ['ParamEyeLOpen','ParamEyeROpen'])if(this.channel.fadingIds.has(id)||this.channel.fadingIds.has('EyeBlink')||bodyClips.some(clip=>clip?.ids.has(id)||clip?.ids.has('EyeBlink'))){const i=this.sampler.index(id);if(i>=0)core.setParameterValueByIndex(i,this.channel.pose[i]);}
   this.sampler.read(this.expressionBase);this.expressionTarget.set(this.expressionBase);
   this.expression?.sample(this.expressionTarget,this.channel.elapsedMs-this.expressionStart,this.expressionBase);
   const weight=this.expressionFade<=0?1:ease((this.channel.elapsedMs-this.expressionStart)/this.expressionFade);
   for(let i=0;i<this.expressionDelta.length;i++){
    this.expressionDelta[i]=this.expressionFrom[i]!+(this.expressionTarget[i]!-this.expressionBase[i]!-this.expressionFrom[i]!)*weight;
    this.expressionTarget[i]=this.expressionBase[i]!+this.expressionDelta[i]!;
   }
   this.sampler.write(this.expressionTarget);
   internal.focusController.update(dt);
   const focus=internal.focusController;this.gazeWeight+=((this.channel.activeGesture?0.15:0.35)-this.gazeWeight)*(1-Math.exp(-dt/150));const headWeight=this.gazeWeight;
   for(const [id,value]of [['ParamEyeBallX',focus.x],['ParamEyeBallY',focus.y],['ParamAngleX',focus.x*10*headWeight],['ParamAngleY',focus.y*10*headWeight]] as const){const i=this.sampler.index(id);if(i>=0)core.addParameterValueByIndex(i,value);}
   const breath=this.sampler.index('ParamBreath');const baseline=this.channel.currentBaseline as SampledClip|undefined;
   if(breath>=0&&!this.channel.fadingIds.has('ParamBreath')&&!baseline?.ids.has('ParamBreath')&&!(this.channel.currentGesture as SampledClip|undefined)?.ids.has('ParamBreath'))core.setParameterValueByIndex(breath,0.5+Math.sin(seconds*2*Math.PI/4)*0.25);
   this.mouth+=(this.mouthTarget-this.mouth)*(1-Math.exp(-dt/(this.mouthTarget>this.mouth?35:90)));
   const mouthIndex=this.sampler.index('ParamMouthOpenY');const lipOwned=this.voiceOwned||this.mouth>0.001||this.mouthTarget>0;
   if(mouthIndex>=0&&lipOwned)core.setParameterValueByIndex(mouthIndex,this.mouth);
   for(let remaining=dt;remaining>0;){const step=Math.min(1000/60,remaining);internal.physics?.evaluate(core,step/1000);internal.pose?.updateParameters(core,step/1000);remaining-=step;}
   // Expression eye closure and TTS opening have final ownership over conflicting physics curves.
   for(const id of ['ParamEyeLOpen','ParamEyeROpen'])if(this.expressionOwnedIds.has(id)){const i=this.sampler.index(id);if(i>=0)core.setParameterValueByIndex(i,this.expressionTarget[i]);}
   if(mouthIndex>=0&&lipOwned)core.setParameterValueByIndex(mouthIndex,this.mouth);
   if(weight>=1&&this.expressionSettling){this.expressionOwnedIds.clear();for(const id of this.expression?.ids??[])this.expressionOwnedIds.add(id);this.expressionSettling=false;}
   core.update();this.diagnostics.updates++;
   if(this.wasGesture&&!this.channel.activeGesture)this.diagnostics.finishes++;this.wasGesture=this.channel.activeGesture;
  }catch(error){this.report(error);this.channel.cancel();this.setExpression(undefined);}
 }
 private report(error:unknown){this.diagnostics.resourceErrors++;if(this.diagnostics.resourceErrors<=5)this.onError(error instanceof Error?error.message:'动画不可用');}
 dispose(){if(this.disposed)return;this.disposed=true;this.abort.abort();this.director.clear();this.previewEpoch++;this.stateEpoch++;this.app.ticker.remove(this.tick,this);this.clips.clear();this.residentClips.clear();this.pending.clear();removeAndDestroyLive2DModel(this.app,this.model);avatarInstances.destroyed++;avatarInstances.active--;}
}
export async function createAvatarPlayer(app:Application,config:PlayerConfig,onError?:(message:string)=>void):Promise<AvatarPlayer>{
 const model=await loadLive2DModel(config.modelPath);
 try{const sdk=await import('pixi-live2d-display/cubism4');return new AvatarPlayer(app,model,config,sdk,onError);}catch(error){removeAndDestroyLive2DModel(app,model);throw error;}
}
