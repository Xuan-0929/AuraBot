/** Selects one verified resident candidate per state round using the player's existing clock.
 * It owns no timers or resources: AvatarPlayer supplies loaded clips and applies each selection.
 * SAY/preview suspension preserves round age; state changes while suspended collapse to the latest. */
import type {AvatarAdaptor,AvatarState} from '../../shared/contracts/avatar';
interface Candidate {identity?:string;kind:'motion'|'expression';durationMs:number;fadeInMs:number;fadeOutMs?:number;}
export class ResidentScheduler {
 private states:AvatarAdaptor['states']={idle:[],listening:[],thinking:[],speaking:[]};
 private available=new Map<string,Candidate>();
 private state:AvatarState='idle';
 private appliedState?:AvatarState;
 private paused=false;
 private remainingMs=Infinity;
 private fingerprint='';
 current?:string;
 constructor(private readonly start:(id:string|undefined)=>void,private readonly random:()=>number=Math.random){}
 get remaining(){return this.paused?Infinity:this.remainingMs;}
 configure(states:AvatarAdaptor['states'],available:Map<string,Candidate>){
  const fingerprint=JSON.stringify([states,[...available]]);
  if(this.current&&this.available.get(this.current)?.identity!==available.get(this.current)?.identity)this.current=undefined;
  this.states={idle:[...states.idle],listening:[...states.listening],thinking:[...states.thinking],speaking:[...states.speaking]};this.available=available;
  if(this.fingerprint===fingerprint)return;
  this.fingerprint=fingerprint;this.appliedState=undefined;if(!this.paused)this.draw();
 }
 setState(state:AvatarState){this.state=state;if(!this.paused&&this.appliedState!==state)this.draw();}
 pause(value:boolean){this.paused=value;if(!value&&this.appliedState!==this.state)this.draw();}
 restart(){this.appliedState=undefined;if(!this.paused)this.draw();}
 advance(deltaMs:number){
  if(this.paused||!Number.isFinite(deltaMs)||deltaMs<=0)return;
  this.remainingMs-=deltaMs;
  if(this.remainingMs<=1e-7)this.draw();
 }
 private draw(){
  let pool=this.states[this.state].filter(id=>this.available.has(id));
  if(!pool.length&&this.state!=='idle')pool=this.states.idle.filter(id=>this.available.has(id));
  const previous=this.current;
  const value=this.random();const index=Math.min(pool.length-1,Math.floor(Math.max(0,Number.isFinite(value)?value:0)*pool.length));
  this.current=pool[index];this.appliedState=this.state;
  const clip=this.current?this.available.get(this.current):undefined;
  this.remainingMs=!clip?Infinity:clip.kind==='expression'?5000+(previous===this.current?0:Math.max(250,clip.fadeInMs,this.available.get(previous??'')?.fadeOutMs??0)):Math.max(1,clip.durationMs);
  this.start(this.current);
 }
}
