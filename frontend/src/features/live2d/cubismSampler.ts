/** Adapts the pinned Cubism 0.4 Framework evaluator to reusable parameter/part pose buffers.
 * Core supplies real IDs and rest values; Framework alone parses/evaluates curves and expression
 * blend modes. The application channel owns overall transitions, clocks and completion identities.
 * This is the sole version-specific access to Framework's model ID arrays; unknown IDs are rejected
 * before its getParameterIndex can synthesize nonexistent parameters.
 */
import type {PoseClip} from './poseChannel';
export interface SampledClip extends PoseClip { ids:ReadonlySet<string>; kind:'motion'|'expression'; }
const numeric=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value);
const MAX_SEGMENTS=100_000,MAX_POINTS=300_000,MAX_EVENTS=4096;
function validateFade(value:unknown):void {
  if(value!==undefined&&(!numeric(value)||value < -1||value>600))throw new Error('无效资源过渡时间');
}
/** Counts the fixed segment record sizes before Framework allocation; SDK still parses and evaluates.
 * This prevents forged metadata allocating huge arrays and rejects records the SDK cannot advance over.
 */
function validateMotion(data:any,rows:any[]):void {
  const meta=data.Meta;
  if(data.Version!==3||!numeric(meta?.Duration)||meta.Duration<=0||meta.Duration>600)throw new Error('无效动作时长');
  const count=(value:unknown,max:number)=>Number.isInteger(value)&&Number(value)>=0&&Number(value)<=max;
  if(!count(meta.CurveCount,4096)||meta.CurveCount!==rows.length||!count(meta.TotalSegmentCount,MAX_SEGMENTS)||!count(meta.TotalPointCount,MAX_POINTS)||!count(meta.UserDataCount??0,MAX_EVENTS))throw new Error('无效或超限的动作资源计数');
  if(meta.Fps!==undefined&&(!numeric(meta.Fps)||meta.Fps<=0||meta.Fps>1000))throw new Error('无效动作帧率');
  validateFade(meta.FadeInTime);validateFade(meta.FadeOutTime);
  // Cubism exports may round Meta.Duration less precisely than the last frame (Hiyori: 5.93/5.933).
  const timeTolerance=1/(meta.Fps??30)+0.001;
  let segments=0,points=0,previousTarget=-1;
  for(const row of rows){
    const target=['Model','Parameter','PartOpacity'].indexOf(row.Target);
    if(target<0||target<previousTarget)throw new Error('动作曲线类型或顺序不受支持');
    previousTarget=target;validateFade(row.FadeInTime);validateFade(row.FadeOutTime);
    const values=row.Segments;
    if(!Array.isArray(values)||values.length<5||values.length>100000||!values.every(numeric))throw new Error('无效动作曲线段编码');
    let time=values[0];if(time<0||time>meta.Duration)throw new Error('无效动作曲线时间');points++;
    for(let offset=2;offset<values.length;){
      const opcode=values[offset];const size=opcode===1?7:[0,2,3].includes(opcode)?3:0;
      if(!size||offset+size>values.length)throw new Error('无效动作曲线段编码');
      const endTime=values[offset+size-2];
      if(endTime<=time||endTime>meta.Duration+timeTolerance)throw new Error('无效动作曲线时间');
      if(opcode===1&&(values[offset+1]<time||values[offset+1]>endTime||values[offset+3]<time||values[offset+3]>endTime))throw new Error('无效贝塞尔控制点时间');
      time=endTime;segments++;points+=opcode===1?3:1;
      if(segments>MAX_SEGMENTS||points>MAX_POINTS)throw new Error('动作资源计数超过上限');
      offset+=size;
    }
  }
  if(segments!==meta.TotalSegmentCount||points!==meta.TotalPointCount)throw new Error('动作资源计数与曲线不一致');
  const events=data.UserData??[];
  if(!Array.isArray(events)||events.length!==(meta.UserDataCount??0))throw new Error('动作事件计数不一致');
  for(const event of events)if(!numeric(event?.Time)||event.Time<0||event.Time>meta.Duration||typeof event.Value!=='string'||event.Value.length>4096)throw new Error('无效动作事件');
}
export class CubismSampler {
  readonly rest:Float32Array;
  readonly parameterCount:number;
  private readonly ids:Map<string,number>;
  private readonly parts:Set<string>;
  constructor(readonly core:any,private readonly sdk:any){
    this.parameterCount=core.getParameterCount();
    this.ids=new Map<string,number>(core._parameterIds.map((id:string,index:number)=>[id,index]));
    this.parts=new Set<string>(core._partIds);
    this.rest=new Float32Array(this.parameterCount+core.getPartCount());this.read(this.rest);
  }
  index(id:string):number{return this.ids.get(id)??-1;}
  read(out:Float32Array){
    for(let i=0;i<this.parameterCount;i++)out[i]=this.core.getParameterValueByIndex(i);
    for(let i=this.parameterCount;i<out.length;i++)out[i]=this.core.getPartOpacityByIndex(i-this.parameterCount);
  }
  write(values:Float32Array){
    for(let i=0;i<this.parameterCount;i++)this.core.setParameterValueByIndex(i,values[i]);
    for(let i=this.parameterCount;i<values.length;i++)this.core.setPartOpacityByIndex(i-this.parameterCount,values[i]);
  }
  create(data:any,kind:'motion'|'expression',fade?:{fade_in_ms?:number;fade_out_ms?:number}):SampledClip{
    if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('无效动画资源');
    const rows=kind==='motion'?data.Curves:data.Parameters;
    if(!Array.isArray(rows)||rows.length>4096)throw new Error('无效动画参数列表');
    const ids=new Set<string>();
    for(const row of rows){
      if(!row||typeof row.Id!=='string')throw new Error('无效参数 ID');
      if(kind==='motion'&&row.Target==='Model'){
        if(!['EyeBlink','LipSync'].includes(row.Id))throw new Error('不支持的模型曲线');
      }else if(kind==='motion'&&row.Target==='PartOpacity'){
        if(!this.parts.has(row.Id))throw new Error('模型缺少部件参数');
      }else if(!this.ids.has(row.Id))throw new Error('模型缺少资源需要的参数');
      ids.add(row.Id);
    }
    if(kind==='motion')validateMotion(data,rows);
    else for(const row of rows)if(!numeric(row.Value)||row.Blend&&!['Add','Multiply','Overwrite'].includes(row.Blend))throw new Error('无效表情混合参数');
    validateFade(data.FadeInTime);validateFade(data.FadeOutTime);
    for(const override of [fade?.fade_in_ms,fade?.fade_out_ms])if(override!==undefined&&(!numeric(override)||override<0||override>5000))throw new Error('过渡覆盖必须为 0–5000 毫秒');
    const motion=kind==='motion'?this.sdk.CubismMotion.create(data):this.sdk.CubismExpressionMotion.create(data);
    const durationMs=kind==='motion'?motion.getDuration()*1000:Infinity;
    const fadeValue=(override:number|undefined,resource:number|undefined,fallback:number)=>override??(Number.isFinite(resource)&&resource! >= 0?Math.min(5000,resource!*1000):fallback);
    const fadeInMs=fadeValue(fade?.fade_in_ms,data.FadeInTime??data.Meta?.FadeInTime,kind==='motion'?250:200);
    const fadeOutMs=fadeValue(fade?.fade_out_ms,data.FadeOutTime??data.Meta?.FadeOutTime,200);
    motion.setFadeInTime(fadeInMs/1000);motion.setFadeOutTime(fadeOutMs/1000);
    const entry=new this.sdk.CubismMotionQueueEntry();entry.setIsStarted(true);entry.setStartTime(0);entry.setFadeInStartTime(0);entry.setEndTime(-1);
    // Part-opacity evaluation in this exact Framework release is explicitly opt-in.
    this.sdk.CubismConfig.setOpacityFromMotion=true;
    motion.setEffectIds?.([...this.ids.keys()].filter(id=>/^ParamEye[LR]Open$/.test(id)),[]);
    return {kind,ids,durationMs,fadeInMs,fadeOutMs,
      sample:(out,timeMs,base,elapsedMs=timeMs)=>{
        if(!numeric(timeMs)||timeMs<0||!numeric(elapsedMs)||elapsedMs<0)throw new Error('无效动画采样时钟');
        this.write(base);entry.setIsFinished(false);entry.setStartTime(0);
        // Curve phase may wrap; the fade age must remain monotonic across baseline loops.
        entry.setFadeInStartTime((timeMs-elapsedMs)/1000);
        motion.doUpdateParameters(this.core,timeMs/1000,1,entry);
        this.read(out);
        if(!out.every(Number.isFinite))throw new Error('动画求值产生非有限参数');
      }};
  }
}
