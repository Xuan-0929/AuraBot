/** Tracks bounded SAY scopes between delivery events and one player's expression/body layers.
 * Offers only prepare ownership; actual audio consumption or text display starts a gesture once.
 * Closure plus completed clauses releases voice expression, while text uses a bounded local hold.
 * Realtime amendments update a known live scope without delaying audio or resurrecting retired replies.
 */
import type {AvatarCue,AvatarInstruction} from '@shared/contracts/avatar';
export interface PerformanceEvent {
 type:'offer'|'start'|'update'|'complete'|'closed'|'cancel'|'level'|'pause'|'resume'|'reset';
 cue?:AvatarCue;clauseId?:string;clauseCount?:number;mode?:'text'|'voice';text?:string;level?:number;
}
interface Scope {cue:AvatarCue;started:boolean;completed:Set<string>;count?:number;holdUntil:number;mode:'text'|'voice';}
export class PerformanceDirector {
 private scopes=new Map<string,Scope>();
 private seen=new Set<string>();
 private active?:Scope;
 private timeMs=0;
 private paused=false;
 readonly diagnostics={starts:0,finishes:0,dropped:0};
 constructor(private readonly identity:Pick<AvatarCue,'profile_id'|'generation'|'config_revision'>,private readonly output:{start:(avatar:AvatarInstruction)=>void;finish:()=>void;level:(value:number)=>void;voice?:(owned:boolean)=>void}){}
 accept(event:PerformanceEvent){
  if(event.type==='reset'){this.clear();return;}
  if(event.type==='pause'||event.type==='resume'){this.paused=event.type==='pause';if(this.paused)this.output.level(0);return;}
  if(event.type==='level'){
   if(event.cue&&(!this.matches(event.cue)||this.active?.cue.say_id!==event.cue.say_id)){this.diagnostics.dropped++;return;}
   if(!this.paused)this.output.level(Number.isFinite(event.level)?Math.max(0,Math.min(1,event.level!)):0);return;
  }
  const cue=event.cue;
  if(!cue||!this.matches(cue)||this.seen.has(cue.say_id)){this.diagnostics.dropped++;return;}
  let scope=this.scopes.get(cue.say_id);
  if(event.type==='update'){
   if(!scope){this.diagnostics.dropped++;return;}
   if(JSON.stringify(scope.cue.avatar)!==JSON.stringify(cue.avatar)){
    scope.cue=cue;if(scope.started&&this.active===scope)this.output.start(cue.avatar??{});
   }
   return;
  }
  if(!scope){
   if(this.scopes.size>=4){this.diagnostics.dropped++;return;}
   scope={cue,started:false,completed:new Set(),holdUntil:0,mode:event.mode??'voice'};this.scopes.set(cue.say_id,scope);
  }
  if(event.type==='cancel'){this.finish(scope);return;}
  if(event.type==='closed')scope.count=event.clauseCount??scope.completed.size;
  if(event.type==='complete'&&event.clauseId)scope.completed.add(event.clauseId);
  if(event.type==='start'&&!scope.started){
   if(this.paused&&(event.mode??scope.mode)==='voice')return;
   if(this.active&&this.active!==scope)this.finish(this.active);
   this.active=scope;scope.started=true;scope.mode=event.mode??scope.mode;
   scope.holdUntil=this.timeMs+(scope.mode==='text'?Math.min(4000,Math.max(1500,(event.text?.length??0)*50)):0);
   this.output.voice?.(scope.mode==='voice');this.output.start(scope.cue.avatar??{});this.diagnostics.starts++;
  }
  this.maybeFinish(scope);
 }
 advance(deltaMs:number){if(Number.isFinite(deltaMs)&&deltaMs>0)this.timeMs+=deltaMs;for(const scope of this.scopes.values())this.maybeFinish(scope);}
 private maybeFinish(scope:Scope){
  if(scope.started&&scope.mode==='text'&&this.timeMs>=scope.holdUntil)this.finish(scope);
  else if(scope.mode==='voice'&&scope.count!==undefined&&scope.completed.size>=scope.count)this.finish(scope);
 }
 private finish(scope:Scope){
  if(this.active===scope){this.output.finish();this.output.level(0);this.output.voice?.(false);this.active=undefined;this.diagnostics.finishes++;}
  this.scopes.delete(scope.cue.say_id);this.seen.add(scope.cue.say_id);
  if(this.seen.size>128)this.seen.delete(this.seen.values().next().value!);
 }
 private matches(cue:AvatarCue){return cue.profile_id===this.identity.profile_id&&cue.generation===this.identity.generation&&cue.config_revision===this.identity.config_revision;}
 clear(){for(const scope of this.scopes.values())this.finish(scope);this.paused=false;this.output.level(0);this.output.voice?.(false);}
}
