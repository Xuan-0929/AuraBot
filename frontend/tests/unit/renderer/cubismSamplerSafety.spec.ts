/** Validates resource allocation bounds and loop clocks against the pinned real Cubism/Core runtime.
 * Fixtures contain authored constant curves; no SDK parsing, motion evaluation or pose blending is mocked.
 */
import {afterAll,beforeAll,expect,it} from 'vitest';
import {readFileSync,readdirSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
import {CubismSampler} from '../../../src/features/live2d/cubismSampler';
import {PoseChannel} from '../../../src/features/live2d/poseChannel';
let sdk:any,moc:any,core:any,sampler:CubismSampler;
beforeAll(async()=>{
 const global=globalThis as any;global.require=createRequire(import.meta.url);global.__dirname=process.cwd();
 vm.runInThisContext(readFileSync('node_modules/live2dcubismcore/live2dcubismcore.min.js','utf8'));
 global.window={Live2DCubismCore:global.Live2DCubismCore};sdk=await import('pixi-live2d-display/cubism4');
 const bytes=readFileSync('public/assets/live2d/hiyori/Hiyori.moc3');moc=sdk.CubismMoc.create(bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength));core=moc.createModel();sampler=new CubismSampler(core,sdk);
});
afterAll(()=>{core?.release();moc?.release();});
function motion(){return {Version:3,Meta:{Duration:1,Fps:30,Loop:true,CurveCount:1,TotalSegmentCount:1,TotalPointCount:2,FadeInTime:0.8,FadeOutTime:0.4},Curves:[{Target:'Parameter',Id:'ParamAngleX',FadeInTime:0.2,Segments:[0,10,0,1,10]}]};}
it('preserves positive resource fades and treats negative sentinel as the default',()=>{
 const data=motion();const clip=sampler.create(data,'motion');expect(clip.fadeInMs).toBe(800);expect(clip.fadeOutMs).toBe(400);
 data.Meta.FadeInTime=-1;expect(sampler.create(data,'motion').fadeInMs).toBe(250);
 expect(sampler.create(data,'motion',{fade_in_ms:0,fade_out_ms:150}).fadeInMs).toBe(0);
});
it('keeps native per-curve fade monotonic across a continuous loop seam at each frame rate',()=>{
 for(const hz of [30,60,120]){
  const channel=new PoseChannel(sampler.rest);const clip=sampler.create(motion(),'motion');channel.setBaseline(clip);
  for(let i=0;i<hz-1;i++)channel.advance(1000/hz);
  const index=sampler.index('ParamAngleX');const before=channel.pose[index]!;channel.advance(1000/hz);const at=channel.pose[index]!;channel.advance(1000/hz);
  expect(before).toBeCloseTo(10,5);expect(at).toBeCloseTo(10,5);expect(channel.pose[index]).toBeCloseTo(10,5);
 }
});
it('rejects mismatched allocation counts before entering native SDK parsing',()=>{
 const data=motion();data.Meta.TotalPointCount=7;
 expect(()=>sampler.create(data,'motion')).toThrow(/计数/);
});
it('rejects unsupported or truncated curve segments at the application boundary',()=>{
 const unknown=motion();unknown.Curves[0]!.Segments=[0,10,99,1,10];expect(()=>sampler.create(unknown,'motion')).toThrow(/段编码/);
 const short=motion();short.Curves[0]!.Segments=[0,10,1,0.2,10];expect(()=>sampler.create(short,'motion')).toThrow(/段编码/);
});

it('rejects extreme allocation claims, nonfinite counts and invalid temporal records safely',()=>{
 for(const field of ['CurveCount','TotalSegmentCount','TotalPointCount','UserDataCount']){
  for(const value of [1_000_000_000,Infinity,NaN,-1,1.5]){
   const data:any=motion();data.Meta[field]=value;expect(()=>sampler.create(data,'motion')).toThrow(/计数/);
  }
 }
 const repeated=motion();repeated.Curves[0]!.Segments=[0,10,0,0,10];expect(()=>sampler.create(repeated,'motion')).toThrow(/时间/);
 expect(()=>sampler.create(motion(),'motion',{fade_in_ms:NaN})).toThrow(/毫秒/);
});
it('continues to parse and evaluate every bundled real Hiyori motion',()=>{
 const root='public/assets/live2d/hiyori/motions';
 for(const file of readdirSync(root).filter(name=>name.endsWith('.motion3.json'))){
  const data=JSON.parse(readFileSync(`${root}/${file}`,'utf8'));const clip=sampler.create(data,'motion');const out=sampler.rest.slice();
  clip.sample(out,500,sampler.rest);expect(out.every(Number.isFinite)).toBe(true);
 }
});

function constantMotion(curves:Array<{target:'Parameter'|'PartOpacity';id:string;value:number}>,duration=1){
 return {Version:3,Meta:{Duration:duration,Fps:60,Loop:false,CurveCount:curves.length,TotalSegmentCount:curves.length,TotalPointCount:curves.length*2,FadeInTime:0.8,FadeOutTime:0.7},Curves:curves.map(curve=>({Target:curve.target,Id:curve.id,Segments:[0,curve.value,0,duration,curve.value]}))};
}
it('crossfades real parameter and part tracks without inserting default pose and restores old-only controls',()=>{
 const angleX=sampler.index('ParamAngleX'),angleY=sampler.index('ParamAngleY'),part=core.getParameterCount();
 const baseline=sampler.create(constantMotion([{target:'Parameter',id:'ParamAngleX',value:4}],4),'motion',{fade_in_ms:200,fade_out_ms:200});
 const gesture=sampler.create(constantMotion([{target:'Parameter',id:'ParamAngleX',value:15},{target:'Parameter',id:'ParamAngleY',value:20},{target:'PartOpacity',id:core._partIds[0],value:0.2}],0.5),'motion',{fade_in_ms:200,fade_out_ms:200});
 expect(gesture.fadeInMs).toBe(200);expect(gesture.fadeOutMs).toBe(200);
 const channel=new PoseChannel(sampler.rest);channel.setBaseline(baseline);for(let i=0;i<10;i++)channel.advance(20);
 expect(channel.pose[angleX]).toBeCloseTo(4);const entryPose=channel.pose.slice();channel.play(gesture);expect(channel.pose).toEqual(entryPose);
 channel.advance(1);expect(Math.abs(channel.pose[angleX]!-entryPose[angleX]!)).toBeLessThan(0.01);
 for(let i=0;i<10;i++)channel.advance(20);expect(channel.pose[angleX]).toBeCloseTo(15);expect(channel.pose[angleY]).toBeCloseTo(20);expect(channel.pose[part]).toBeCloseTo(0.2);
 const exit:Array<{angle:number;side:number;opacity:number}>=[];
 for(let i=0;i<30;i++){
  channel.advance(20);sampler.write(channel.pose);
  exit.push({angle:core.getParameterValueByIndex(angleX),side:core.getParameterValueByIndex(angleY),opacity:core.getPartOpacityByIndex(0)});
 }
 expect(exit.every(pose=>pose.angle>=4-0.0001&&pose.angle<=15.0001)).toBe(true);
 expect(exit.every(pose=>pose.opacity>=0.1999&&pose.opacity<=sampler.rest[part]!+0.0001)).toBe(true);
 expect(channel.activeGesture).toBe(false);expect(exit.at(-1)?.angle).toBeCloseTo(4);expect(exit.at(-1)?.side).toBeCloseTo(sampler.rest[angleY]!);expect(exit.at(-1)?.opacity).toBeCloseTo(sampler.rest[part]!);
});
it('lets a new real gesture take ownership mid-motion beyond the old completion deadline',()=>{
 const x=sampler.index('ParamAngleX'),y=sampler.index('ParamAngleY'),part=core.getParameterCount();
 const baseline=sampler.create(constantMotion([{target:'Parameter',id:'ParamAngleX',value:4}],4),'motion',{fade_in_ms:200,fade_out_ms:200});
 const old=sampler.create(constantMotion([{target:'Parameter',id:'ParamAngleX',value:15},{target:'Parameter',id:'ParamAngleY',value:20},{target:'PartOpacity',id:core._partIds[0],value:0.2}],0.5),'motion',{fade_in_ms:200,fade_out_ms:200});
 const next=sampler.create(constantMotion([{target:'Parameter',id:'ParamAngleX',value:-8}],1.2),'motion',{fade_in_ms:200,fade_out_ms:200});
 const channel=new PoseChannel(sampler.rest);channel.setBaseline(baseline);for(let i=0;i<10;i++)channel.advance(20);channel.play(old);for(let i=0;i<15;i++)channel.advance(20);
 const before=channel.pose.slice();channel.play(next);expect(channel.pose).toEqual(before);channel.advance(1);expect(Math.abs(channel.pose[x]!-before[x]!)).toBeLessThan(0.01);
 for(let i=0;i<20;i++)channel.advance(20);
 expect(channel.activeGesture).toBe(true);expect(channel.pose[x]).toBeCloseTo(-8);expect(channel.pose[y]).toBeCloseTo(sampler.rest[y]!);expect(channel.pose[part]).toBeCloseTo(sampler.rest[part]!);
 for(let i=0;i<55;i++)channel.advance(20);
 expect(channel.activeGesture).toBe(false);expect(channel.pose[x]).toBeCloseTo(4);
});

it('unions outgoing real parameter masks through rapid replacement and releases them after the bounded fade',()=>{
 const ids=['ParamEyeLOpen','ParamBreath','ParamAngleX'];
 const clips=ids.map(id=>sampler.create(constantMotion([{target:'Parameter',id,value:0}],4),'motion',{fade_in_ms:200,fade_out_ms:200}));
 const channel=new PoseChannel(sampler.rest);channel.play(clips[0]!);channel.advance(100);channel.advance(100);
 channel.play(clips[1]!);channel.advance(50);channel.play(clips[2]!);
 expect(channel.fadingIds.has('ParamEyeLOpen')).toBe(true);expect(channel.fadingIds.has('ParamBreath')).toBe(true);
 for(let i=0;i<100;i++){channel.play(clips[i%3]!);channel.advance(1);expect(channel.fadingIds.size).toBeLessThanOrEqual(ids.length);}
 channel.advance(100);channel.advance(100);expect(channel.fadingIds.size).toBe(0);
});
