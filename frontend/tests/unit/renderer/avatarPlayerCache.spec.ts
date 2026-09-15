/** Runs the production player with the real Cubism/Core evaluator to protect resident baseline phase.
 * Playback gates must not freeze unowned resident actions when Backend VAD changes state.
 * Fetch supplies a bounded authored motion; GPU rendering is covered separately by Electron tests.
 */
import {afterAll,afterEach,beforeAll,expect,it,vi} from 'vitest';
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
afterEach(()=>vi.restoreAllMocks());
it('retains active resource identity and its continuing phase when only the Profile revision changes',async()=>{
 const core=moc.createModel();const settings=new sdk.Cubism4ModelSettings({...JSON.parse(readFileSync('public/assets/live2d/hiyori/Hiyori.model3.json','utf8')),url:'http://localhost/Hiyori.model3.json'});
 const internal=new sdk.Cubism4InternalModel(core,settings,{motionPreload:'NONE'});internal.eyeBlink.setBlinkingInterval(100);const ticks=new Set();
 const app={stage:{addChild:()=>{},removeChild:()=>{}},ticker:{add:(f:unknown)=>ticks.add(f),remove:(f:unknown)=>ticks.delete(f)}};
 const model={internalModel:internal,autoUpdate:false,destroy:()=>internal.destroy()};
 const data={Version:3,Meta:{Duration:4,Fps:60,Loop:true,CurveCount:1,TotalSegmentCount:1,TotalPointCount:2,FadeInTime:0.2,FadeOutTime:0.2},Curves:[{Target:'Parameter',Id:'ParamAngleX',Segments:[0,1,0,4,9]}]};
 const fetch=vi.fn(async()=>({ok:true,text:async()=>JSON.stringify(data)}));vi.stubGlobal('fetch',fetch);vi.stubGlobal('location',{href:'http://localhost/'});
 const adaptor=emptyAvatarAdaptor();adaptor.model={manifest:'model/Hiyori.model3.json'};adaptor.entries.idle={kind:'motion',source:{file:'model/idle.motion3.json'},description:'authored fixture',agent_enabled:false,playback:{mode:'loop'}};adaptor.states.idle=['idle'];adaptor.validated_resources=['model/idle.motion3.json'];
 const config={modelPath:'/Hiyori.model3.json',profileId:'a',revision:1,modelVersion:'v',generation:1,adaptor};const beforeInstances=avatarInstances.active;
 const player=new AvatarPlayer(app as any,model as any,config,sdk);
 try{
  await player.prepare();
  player.accept({type:'pause'});await player.setState('listening');
  const phase=player.channel.elapsedMs;player.advance(100);
  expect(player.channel.elapsedMs).toBeGreaterThan(phase); // No SAY owner: resident remains alive under the audio gate.
  player.accept({type:'resume'});await player.setState('idle');
  for(let i=0;i<12;i++)player.advance(100);
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

function residentFixture(random:()=>number=()=>0){
 vi.spyOn(Math,'random').mockReturnValue(.5);
 const core=moc.createModel();const settings=new sdk.Cubism4ModelSettings({...JSON.parse(readFileSync('public/assets/live2d/hiyori/Hiyori.model3.json','utf8')),url:'http://localhost/Hiyori.model3.json'});
 const internal=new sdk.Cubism4InternalModel(core,settings,{motionPreload:'NONE'});internal.eyeBlink.setBlinkingInterval(100);const ticks=new Set();
 const app={stage:{addChild:()=>{},removeChild:()=>{}},ticker:{add:(f:unknown)=>ticks.add(f),remove:(f:unknown)=>ticks.delete(f)}};
 const model={internalModel:internal,autoUpdate:false,destroy:()=>internal.destroy()};
 const motion=(start:number,end:number,part=false)=>({Version:3,Meta:{Duration:1,Fps:60,Loop:false,CurveCount:part?2:1,TotalSegmentCount:part?2:1,TotalPointCount:part?4:2,FadeInTime:0,FadeOutTime:0},Curves:[{Target:'Parameter',Id:'ParamAngleX',Segments:[0,start,0,1,end]},...(part?[{Target:'PartOpacity',Id:core._partIds[0],Segments:[0,.2,0,1,.2]}]:[])]});
 const files:Record<string,unknown>={'a.motion3.json':motion(10,20,true),'b.motion3.json':motion(-10,-5),'face.exp3.json':{Type:'Live2D Expression',Parameters:[{Id:'ParamEyeLOpen',Value:0,Blend:'Overwrite'}]}};
 const adaptor=emptyAvatarAdaptor();adaptor.model={manifest:'model/Hiyori.model3.json'};
 for(const [id,kind]of [['a','motion'],['b','motion'],['face','expression']] as const){const file=`model/${id}.${kind==='motion'?'motion3':'exp3'}.json`;adaptor.entries[id]={kind,source:{file},description:'',display_name:id,agent_enabled:false,playback:{mode:kind==='motion'?'loop':'hold'}};}
 adaptor.entries.say={...adaptor.entries.b!,agent_enabled:true,playback:{mode:'once'}};
 adaptor.validated_resources=Object.values(adaptor.entries).map(e=>e.source.file);adaptor.states.idle=['a','b'];
 vi.stubGlobal('fetch',async(url:string)=>({ok:true,text:async()=>JSON.stringify(files[decodeURIComponent(url.split('/').at(-1)!)])}));vi.stubGlobal('location',{href:'http://localhost/'});
 const config={modelPath:'/Hiyori.model3.json',profileId:'a',revision:1,modelVersion:'v',generation:1,adaptor};
 const p=new AvatarPlayer(app as any,model as any,config,sdk,()=>{},random);
 return {p,core,config,ticks};
}
it('crossfades actual Core motion endpoints and exclusive part opacity without rest frames at 30/60/120 Hz',async()=>{
 const results:number[][]=[];
 for(const hz of [30,60,120]){let n=0;const {p,core,ticks}=residentFixture(()=>n++%2?0.99:0);
  try{await p.prepare();const angle=p.sampler.index('ParamAngleX');const trajectory:number[]=[];const steps=hz*3;
   for(let i=1;i<=steps;i++){const before=p.channel.pose[angle]!;p.advance(1000/hz);const after=p.channel.pose[angle]!;expect(Math.abs(after-before)).toBeLessThan(7);if(i%(hz/10)===0)trajectory.push(after);}
   // Endpoints are retained on the switching frame, including the second transition back to A.
   expect(trajectory[9]).toBeCloseTo(20,3);expect(trajectory[19]).toBeCloseTo(-5,3);
   expect(trajectory[10]).toBeGreaterThan(0);expect(ticks.size).toBe(1);
   expect(p.diagnostics.residentStarts).toBe(4);expect(p.diagnostics.resourceErrors).toBe(0);results.push(trajectory);
   expect(Number.isFinite(core.getPartOpacityByIndex(0))).toBe(true);
  }finally{p.dispose();vi.unstubAllGlobals();}
 }
 for(const values of results.slice(1))values.forEach((value,i)=>expect(value).toBeCloseTo(results[0]![i]!,3));
});
it('repeats the same native motion through a fade instead of snapping its endpoint to its first frame',async()=>{
 const {p}=residentFixture();try{await p.prepare();for(let i=0;i<10;i++)p.advance(100);const angle=p.sampler.index('ParamAngleX');expect(p.channel.pose[angle]).toBeCloseTo(20,3);p.advance(1000/120);expect(p.channel.pose[angle]).toBeGreaterThan(19);expect(p.diagnostics.residentStarts).toBe(2);}finally{p.dispose();vi.unstubAllGlobals();}
});
it('holds resident expressions across SAY and restores neutral smoothly after the scope ends',async()=>{
 const {p,core,config}=residentFixture();config.adaptor.states.idle=['face'];
 try{await p.prepare();for(let i=0;i<10;i++)p.advance(100);expect(core.getParameterValueById('ParamEyeLOpen')).toBeCloseTo(0);
  const cue={profile_id:'a',generation:1,config_revision:1,say_id:'s',invoke_id:'i',call_id:'c',avatar:{motion:'say',expression:'neutral'}};
  p.accept({type:'start',cue,mode:'voice'});const draws=p.diagnostics.residentDraws;for(let i=0;i<60;i++)p.advance(100);expect(p.diagnostics.residentDraws).toBe(draws);expect(core.getParameterValueById('ParamEyeLOpen')).toBeGreaterThan(.9);
  p.accept({type:'complete',cue,clauseId:'1'});p.accept({type:'closed',cue,clauseCount:1});p.advance(1000/120);expect(core.getParameterValueById('ParamEyeLOpen')).toBeGreaterThan(.9);
  for(let i=0;i<5;i++)p.advance(100);expect(core.getParameterValueById('ParamEyeLOpen')).toBeCloseTo(0);expect(p.diagnostics.residentDraws).toBe(draws);
  p.accept({type:'cancel',cue});expect(p.diagnostics.residentDraws).toBe(draws);expect(p.diagnostics.resourceErrors).toBe(0);
 }finally{p.dispose();vi.unstubAllGlobals();}
});
it('drops a superseded resident preload while preserving the current body and excluding failed assets',async()=>{
 const {p,config}=residentFixture();try{
  await p.prepare();for(let i=0;i<5;i++)p.advance(100);const prior=p.channel.currentBaseline;
  const originalFetch=globalThis.fetch;let release!:(value:unknown)=>void;const slow=new Promise(resolve=>{release=resolve;});
  vi.stubGlobal('fetch',async(url:string)=>url.includes('slow')?await slow:url.includes('broken')?{ok:false}:originalFetch(url));
  const second=structuredClone(config);second.revision=2;second.adaptor.revision=2;second.adaptor.entries.slow={...second.adaptor.entries.a!,source:{file:'model/slow.motion3.json'}};second.adaptor.validated_resources!.push('model/slow.motion3.json');second.adaptor.states.idle=['slow'];
  const pending=p.configure(second);await Promise.resolve();expect(p.channel.currentBaseline).toBe(prior);
  const third=structuredClone(config);third.revision=3;third.adaptor.revision=3;third.adaptor.entries.broken={...third.adaptor.entries.a!,source:{file:'model/broken.motion3.json'}};third.adaptor.validated_resources!.push('model/broken.motion3.json');third.adaptor.states.idle=['b','broken'];
  await p.configure(third);const current=p.channel.currentBaseline;expect(current).not.toBe(prior);expect(p.diagnostics.residentFailures).toBe(1);
  release(await originalFetch('http://localhost/a.motion3.json'));await pending;expect(p.channel.currentBaseline).toBe(current);expect(p.diagnostics.residentStale).toBeGreaterThan(0);
  await p.prepare();expect(p.diagnostics.residentFailures).toBe(1);for(let i=0;i<15;i++)p.advance(100);expect(p.channel.currentBaseline).toBe(current);
  const draws=p.diagnostics.residentDraws;for(const dt of [NaN,Infinity,-1,0])p.advance(dt);expect(p.diagnostics.residentDraws).toBe(draws);const elapsed=p.channel.elapsedMs;p.advance(100000);expect(p.channel.elapsedMs-elapsed).toBeCloseTo(100);
 }finally{p.dispose();vi.unstubAllGlobals();}
});
it('transitions motion → resident expression → motion with continuous body/part values and one ticker',async()=>{
 let choice=0;const {p,core,config,ticks}=residentFixture(()=>choice);config.adaptor.states.idle=['a','face'];
 try{await p.prepare();for(let i=0;i<9;i++)p.advance(100);choice=.99;p.advance(100);const angle=p.sampler.index('ParamAngleX'),part=core.getParameterCount();expect(p.channel.pose[angle]).toBeCloseTo(20,3);const last=p.channel.pose.slice();p.advance(1000/120);expect(Math.abs(p.channel.pose[angle]!-last[angle]!)).toBeLessThan(.1);expect(p.channel.pose[part]).toBeCloseTo(.2,3);
  for(let i=0;i<5;i++)p.advance(100);expect(core.getParameterValueById('ParamEyeLOpen')).toBeCloseTo(0);choice=0;
  for(let i=0;i<48;i++)p.advance(100);expect(p.diagnostics.residentStarts).toBe(3);expect(core.getParameterValueById('ParamEyeLOpen')).toBeLessThan(.2);for(let i=0;i<4;i++)p.advance(100);expect(core.getParameterValueById('ParamEyeLOpen')).toBeGreaterThan(.9);expect(ticks.size).toBe(1);
 }finally{p.dispose();vi.unstubAllGlobals();}
});
it('does not publish a pending state selection while disposing an active SAY',async()=>{
 const {p}=residentFixture();const changed=vi.fn();p.config.onPlaybackChange=changed;await p.prepare();
 const cue={profile_id:'a',generation:1,config_revision:1,say_id:'s',invoke_id:'i',call_id:'c'};
 p.accept({type:'start',cue,mode:'voice'});await p.setState('thinking');const calls=changed.mock.calls.length;p.dispose();expect(changed).toHaveBeenCalledTimes(calls);p.accept({type:'cancel',cue});expect(changed).toHaveBeenCalledTimes(calls);vi.unstubAllGlobals();
});
it('resumes resident rounds after a candidate pause is followed by confirmed SAY cancellation',async()=>{
 const {p}=residentFixture();try{await p.prepare();const cue={profile_id:'a',generation:1,config_revision:1,say_id:'cancelled',invoke_id:'i',call_id:'c'};
 p.accept({type:'start',cue,mode:'voice'});p.accept({type:'pause'});const draws=p.diagnostics.residentDraws;for(let i=0;i<15;i++)p.advance(100);expect(p.diagnostics.residentDraws).toBe(draws);
 p.accept({type:'cancel',cue});for(let i=0;i<11;i++)p.advance(100);expect(p.diagnostics.residentDraws).toBeGreaterThan(draws);
 }finally{p.dispose();vi.unstubAllGlobals();}
});
it('applies a changed source under the same stable ID even when native clip timing is identical',async()=>{
 const {p,config}=residentFixture();config.adaptor.states.idle=['a'];try{await p.prepare();for(let i=0;i<4;i++)p.advance(100);const before=p.channel.currentBaseline;const changed=structuredClone(config);changed.revision=2;changed.adaptor.revision=2;changed.adaptor.entries.a!.source.file='model/b.motion3.json';await p.configure(changed);expect(p.channel.currentBaseline).not.toBe(before);}finally{p.dispose();vi.unstubAllGlobals();}
});

it('freezes an owned SAY on VAD pause, resumes its phase on noise, and releases listening on confirmation',async()=>{
 const {p,config}=residentFixture();config.adaptor.states.listening=['b'];
 const cue={profile_id:'a',generation:1,config_revision:1,say_id:'vad-say',invoke_id:'i',call_id:'c',avatar:{motion:'say'}};
 try{
  await p.prepare();p.accept({type:'start',cue,clauseId:'c',mode:'voice'});p.advance(100);
  p.accept({type:'pause'});await p.setState('listening');
  const phase=p.channel.elapsedMs;const starts=p.diagnostics.residentStarts;
  p.advance(100);expect(p.channel.elapsedMs).toBe(phase);expect(p.diagnostics.residentStarts).toBe(starts);
  await p.setState('speaking');p.accept({type:'resume'});p.advance(100);expect(p.channel.elapsedMs).toBeGreaterThan(phase);
  p.accept({type:'pause'});await p.setState('listening');p.accept({type:'cancel',cue});
  expect(p.diagnostics.residentStarts).toBeGreaterThan(starts);
  const listeningPhase=p.channel.elapsedMs;p.advance(100);expect(p.channel.elapsedMs).toBeGreaterThan(listeningPhase);
 }finally{p.dispose();vi.unstubAllGlobals();}
});
