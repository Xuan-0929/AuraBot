/** Runs the production player with the real Cubism/Core evaluator to protect resident baseline phase.
 * Fetch supplies a bounded authored motion; GPU rendering is covered separately by Electron tests.
 */
import {afterAll,beforeAll,expect,it,vi} from 'vitest';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
import {AvatarPlayer,avatarInstances} from '../../../src/features/live2d/player';
import {emptyAvatarAdaptor} from '../../../src/shared/contracts/avatar';
let sdk:any,moc:any;
beforeAll(async()=>{
 const global=globalThis as any;global.require=createRequire(import.meta.url);global.__dirname=process.cwd();
 vm.runInThisContext(readFileSync('node_modules/live2dcubismcore/live2dcubismcore.min.js','utf8'));
 global.window={Live2DCubismCore:global.Live2DCubismCore};sdk=await import('pixi-live2d-display/cubism4');
 const bytes=readFileSync('public/assets/live2d/hiyori/Hiyori.moc3');moc=sdk.CubismMoc.create(bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength));
});
afterAll(()=>moc?.release());
it('retains active resource identity and its continuing phase when only the Profile revision changes',async()=>{
 const core=moc.createModel();const settings=new sdk.Cubism4ModelSettings({...JSON.parse(readFileSync('public/assets/live2d/hiyori/Hiyori.model3.json','utf8')),url:'http://localhost/Hiyori.model3.json'});
 const internal=new sdk.Cubism4InternalModel(core,settings,{motionPreload:'NONE'});const ticks=new Set();
 const app={stage:{addChild:()=>{},removeChild:()=>{}},ticker:{add:(f:unknown)=>ticks.add(f),remove:(f:unknown)=>ticks.delete(f)}};
 const model={internalModel:internal,autoUpdate:false,destroy:()=>internal.destroy()};
 const data={Version:3,Meta:{Duration:4,Fps:60,Loop:true,CurveCount:1,TotalSegmentCount:1,TotalPointCount:2,FadeInTime:0.2,FadeOutTime:0.2},Curves:[{Target:'Parameter',Id:'ParamAngleX',Segments:[0,1,0,4,9]}]};
 const fetch=vi.fn(async()=>({ok:true,text:async()=>JSON.stringify(data)}));vi.stubGlobal('fetch',fetch);vi.stubGlobal('location',{href:'http://localhost/'});
 const adaptor=emptyAvatarAdaptor();adaptor.model={manifest:'model/Hiyori.model3.json'};adaptor.entries.idle={kind:'motion',source:{file:'model/idle.motion3.json'},description:'authored fixture',agent_enabled:false,playback:{mode:'loop'}};adaptor.states.idle='idle';
 const config={modelPath:'/Hiyori.model3.json',profileId:'a',revision:1,modelVersion:'v',generation:1,adaptor};const beforeInstances=avatarInstances.active;
 const player=new AvatarPlayer(app as any,model as any,config,sdk);
 try{
  await player.prepare();for(let i=0;i<12;i++)player.advance(100);
  const baseline=player.channel.currentBaseline;const elapsed=player.channel.elapsedMs;const angle=player.sampler.index('ParamAngleX');expect(player.channel.pose[angle]).toBeCloseTo(3.4,4);
  await player.configure({...config,revision:2,generation:2,adaptor:{...structuredClone(adaptor),revision:2}});
  expect(fetch).toHaveBeenCalledOnce();expect(player.channel.currentBaseline).toBe(baseline);expect(player.channel.elapsedMs).toBe(elapsed);
  player.advance(100);expect(player.channel.pose[angle]).toBeCloseTo(3.6,4);expect(ticks.size).toBe(1);expect(avatarInstances.active).toBe(beforeInstances+1);
 }finally{player.dispose();vi.unstubAllGlobals();}
 expect(ticks.size).toBe(0);expect(avatarInstances.active).toBe(beforeInstances);
});

it('keeps outgoing eye and breath controls during the real SDK motion exit fade',async()=>{
 const core=moc.createModel();const settings=new sdk.Cubism4ModelSettings({...JSON.parse(readFileSync('public/assets/live2d/hiyori/Hiyori.model3.json','utf8')),url:'http://localhost/Hiyori.model3.json'});
 const internal=new sdk.Cubism4InternalModel(core,settings,{motionPreload:'NONE'});internal.eyeBlink.setBlinkingInterval(100);
 const random=vi.spyOn(Math,'random').mockReturnValue(0.5);
 const app={stage:{addChild:()=>{},removeChild:()=>{}},ticker:{add:()=>{},remove:()=>{}}};const model={internalModel:internal,autoUpdate:false,destroy:()=>internal.destroy()};
 const data={Version:3,Meta:{Duration:0.4,Fps:60,Loop:false,CurveCount:2,TotalSegmentCount:2,TotalPointCount:4,FadeInTime:0.2,FadeOutTime:0.2},Curves:[{Target:'Parameter',Id:'ParamEyeLOpen',Segments:[0,0,0,0.4,0]},{Target:'Parameter',Id:'ParamBreath',Segments:[0,0.1,0,0.4,0.1]}]};
 vi.stubGlobal('fetch',async()=>({ok:true,text:async()=>JSON.stringify(data)}));vi.stubGlobal('location',{href:'http://localhost/'});
 const player=new AvatarPlayer(app as any,model as any,{modelPath:'/Hiyori.model3.json'},sdk);
 try{
  await player.play({motion:'close.motion3.json'});for(let i=0;i<3;i++)player.advance(100);
  expect(core.getParameterValueById('ParamEyeLOpen')).toBeCloseTo(0);player.advance(100);expect(player.channel.activeGesture).toBe(false);
  expect(core.getParameterValueById('ParamEyeLOpen')).toBeCloseTo(0);
  player.advance(50);const eye=core.getParameterValueById('ParamEyeLOpen');expect(eye).toBeGreaterThan(0);expect(eye).toBeLessThan(0.5);
  expect(core.getParameterValueById('ParamBreath')).toBeCloseTo(player.channel.pose[player.sampler.index('ParamBreath')]!);
  for(let i=0;i<4;i++)player.advance(50);expect(core.getParameterValueById('ParamEyeLOpen')).toBeCloseTo(1);expect(core.getParameterValueById('ParamBreath')).toBeGreaterThan(0.5);
 }finally{player.dispose();random.mockRestore();vi.unstubAllGlobals();}
});
