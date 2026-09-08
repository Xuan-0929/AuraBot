/** Owns the one bounded body channel and its millisecond clock for every avatar instance.
 * SDK samplers produce poses against a stable rest/baseline buffer; timed weights enter from the
 * last body pose and return gesture-only parameters/parts to the continuing baseline. No frame-wise
 * low-pass, parameter accumulation, random idle, callbacks or motion backlog lives in this channel.
 * A bounded union of outgoing parameter IDs preserves control during fades without retaining old clips.
 */
export interface PoseClip {
  ids?:ReadonlySet<string>;
  durationMs:number;
  fadeInMs:number;
  fadeOutMs:number;
  sample(out:Float32Array,timeMs:number,base:Float32Array,elapsedMs?:number):void;
}
export const ease=(value:number)=>0.5-0.5*Math.cos(Math.PI*Math.min(1,Math.max(0,value)));
export class PoseChannel {
  readonly pose:Float32Array;
  elapsedMs=0;
  private readonly baselinePose:Float32Array;
  private readonly target:Float32Array;
  private readonly source:Float32Array;
  private baseline?:PoseClip;
  private baselineStarted=0;
  private gesture?:PoseClip;
  private gestureStarted=0;
  private transitionStarted=0;
  private transitionMs=0;
  private readonly outgoingIds=new Set<string>();
  constructor(private readonly rest:Float32Array){
    this.pose=rest.slice();this.baselinePose=rest.slice();this.target=rest.slice();this.source=rest.slice();
  }
  get fadingIds():ReadonlySet<string>{return this.outgoingIds;}
  get currentGesture(){return this.gesture;}
  get currentBaseline(){return this.baseline;}
  get activeGesture():boolean{return Boolean(this.gesture);}
  setBaseline(clip:PoseClip|undefined){
    if(this.baseline===clip)return;
    const outgoing=this.baseline;this.baseline=clip;this.baselineStarted=this.elapsedMs;
    this.transition(clip?.fadeInMs??250,outgoing);
  }
  play(clip:PoseClip){const outgoing=this.gesture;this.gesture=clip;this.gestureStarted=this.elapsedMs;this.transition(clip.fadeInMs,outgoing);}
  cancel(durationMs=150){const outgoing=this.gesture;this.gesture=undefined;this.transition(durationMs,outgoing);}
  private transition(durationMs:number,outgoing?:PoseClip){
    this.source.set(this.pose);this.transitionStarted=this.elapsedMs;this.transitionMs=durationMs;
    // Rapid replacement unions IDs only. Validated clips cannot exceed the real model ID set.
    if(durationMs<=0)this.outgoingIds.clear();else for(const id of outgoing?.ids??[])this.outgoingIds.add(id);
  }
  advance(deltaMs:number):number{
    const dt=Number.isFinite(deltaMs)&&deltaMs>0?Math.min(deltaMs,100):0;
    if(!dt)return 0;
    this.elapsedMs+=dt;
    this.baselinePose.set(this.rest);
    if(this.baseline){const duration=Math.max(1,this.baseline.durationMs);const elapsed=this.elapsedMs-this.baselineStarted;
      // Keep a stable phase even when floating-point addition lands just below a loop boundary.
      const time=(elapsed+1e-7)%duration;this.baseline.sample(this.baselinePose,time,this.rest,elapsed);}
    if(this.gesture&&this.elapsedMs-this.gestureStarted>=this.gesture.durationMs){
      const outgoing=this.gesture;this.gesture=undefined;this.transition(outgoing.fadeOutMs,outgoing);
    }
    this.target.set(this.baselinePose);
    if(this.gesture)this.gesture.sample(this.target,this.elapsedMs-this.gestureStarted,this.baselinePose);
    const weight=this.transitionMs<=0?1:ease((this.elapsedMs-this.transitionStarted)/this.transitionMs);
    for(let i=0;i<this.pose.length;i++)this.pose[i]=this.source[i]!+(this.target[i]!-this.source[i]!)*weight;
    if(weight>=1)this.outgoingIds.clear();
    return dt;
  }
}
