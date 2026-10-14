/** Runs pinned Cubism Framework motion/expression evaluation against the real Hiyori Core model.
 * Also checks gaze response timing at 30/60/120 Hz and bounded gaze gain on actual Core parameters without replacing the player.
 * Speech-mouth gain is tested against real Core using output-level events. No renderer or curve evaluator is mocked; GPU drawing is separately covered by Electron E2E.
 */
import {beforeAll,afterAll,it,expect,vi} from 'vitest';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
import {CubismSampler} from '../../../src/features/live2d/cubismSampler';
import {AvatarPlayer,avatarInstances} from '../../../src/features/live2d/player';
let sdk:any, moc:any, core:any, sampler:CubismSampler;
beforeAll(async()=>{
 const global=globalThis as any;global.require=createRequire(import.meta.url);global.__dirname=process.cwd();
 vm.runInThisContext(readFileSync('node_modules/live2dcubismcore/live2dcubismcore.min.js','utf8'));
 global.window={Live2DCubismCore:global.Live2DCubismCore};
 sdk=await import('pixi-live2d-display/cubism4');
 const bytes=readFileSync('public/assets/live2d/hiyori/Hiyori.moc3');
 moc=sdk.CubismMoc.create(bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength));core=moc.createModel();sampler=new CubismSampler(core,sdk);
});
afterAll(()=>{core?.release();moc?.release();});
it('samples actual motion curves without accumulating expression or losing baseline',()=>{
 const data=JSON.parse(readFileSync('public/assets/live2d/hiyori/motions/Hiyori_m01.motion3.json','utf8'));
 const motion=sampler.create(data,'motion');const out=sampler.rest.slice();motion.sample(out,500,sampler.rest);
 expect(out.some((value,i)=>Math.abs(value-sampler.rest[i]!)>0.01)).toBe(true);
 const first=out.slice();motion.sample(out,500,sampler.rest);expect(out).toEqual(first);
});
it('evaluates native expression blend and rejects absent parameter IDs',()=>{
 const expression=sampler.create({Type:'Live2D Expression',Parameters:[{Id:'ParamEyeLOpen',Value:0,Blend:'Overwrite'}]},'expression');
 const out=sampler.rest.slice();expression.sample(out,0,sampler.rest);expect(out[sampler.index('ParamEyeLOpen')]).toBe(0);
 expect(()=>sampler.create({Parameters:[{Id:'nonexistent',Value:1}]},'expression')).toThrow(/参数/);
});
it('applies real PartOpacity curves and returns to untouched baseline opacity',()=>{
 const id=core._partIds[0];const motion=sampler.create({Version:3,Meta:{Duration:1,Fps:30,Loop:false,CurveCount:1,TotalSegmentCount:1,TotalPointCount:2},Curves:[{Target:'PartOpacity',Id:id,Segments:[0,0.2,0,1,0.2]}]},'motion');
 const out=sampler.rest.slice();motion.sample(out,500,sampler.rest);expect(out[core.getParameterCount()]).toBeCloseTo(0.2);
 sampler.write(sampler.rest);expect(core.getPartOpacityByIndex(0)).toBeCloseTo(sampler.rest[core.getParameterCount()]);
});
it('production composition keeps expression eyes closed and lets actual TTS own mouth opening',async()=>{
 const c=moc.createModel();const settings=new sdk.Cubism4ModelSettings({...JSON.parse(readFileSync('public/assets/live2d/hiyori/Hiyori.model3.json','utf8')),url:'http://localhost/Hiyori.model3.json'});
 const internal=new sdk.Cubism4InternalModel(c,settings,{motionPreload:'NONE'});
 const callbacks=new Set();const app={stage:{addChild:()=>{},removeChild:()=>{}},ticker:{add:(f:unknown)=>callbacks.add(f),remove:(f:unknown)=>callbacks.delete(f)}};
 const model={internalModel:internal,autoUpdate:false,destroy:()=>internal.destroy()};
 const expression={Type:'Live2D Expression',Parameters:[{Id:'ParamEyeLOpen',Value:0,Blend:'Overwrite'},{Id:'ParamMouthOpenY',Value:0.1,Blend:'Overwrite'}]};
 vi.stubGlobal('location',{href:'http://localhost/'});vi.stubGlobal('fetch',async()=>({ok:true,text:async()=>JSON.stringify(expression)}));
 const before=avatarInstances.active;const p=new AvatarPlayer(app as any,model as any,{modelPath:'/Hiyori.model3.json'},sdk);
 try{
  await p.play({expression:'face.exp3.json'});for(let i=0;i<30;i++)p.advance(1000/60);
  expect(c.getParameterValueById('ParamEyeLOpen')).toBe(0);
  const cue={profile_id:'',generation:0,config_revision:0,say_id:'voice',invoke_id:'i',call_id:'c'};
  p.accept({type:'start',cue,mode:'voice'});p.accept({type:'level',cue,level:0.8});for(let i=0;i<15;i++)p.advance(1000/60);
  expect(c.getParameterValueById('ParamMouthOpenY')).toBeGreaterThan(0.7);
  p.accept({type:'pause'});for(let i=0;i<90;i++)p.advance(1000/60);
  expect(c.getParameterValueById('ParamMouthOpenY')).toBeLessThan(0.01);
  expect(callbacks.size).toBe(1);expect(avatarInstances.active).toBe(before+1);
 }finally{p.dispose();vi.unstubAllGlobals();}
 expect(callbacks.size).toBe(0);expect(avatarInstances.active).toBe(before);
});

it('scales gaze on real Core parameters, disables it at zero and preserves the player clock',()=>{
 const c=moc.createModel();const settings=new sdk.Cubism4ModelSettings({...JSON.parse(readFileSync('public/assets/live2d/hiyori/Hiyori.model3.json','utf8')),url:'http://localhost/Hiyori.model3.json'});
 const internal=new sdk.Cubism4InternalModel(c,settings,{motionPreload:'NONE'});
 internal.focusController.update=()=>{};internal.focusController.x=0.2;internal.focusController.y=0.2;
 const app={stage:{addChild:()=>{},removeChild:()=>{}},ticker:{add:()=>{},remove:()=>{}}};
 const p=new AvatarPlayer(app as any,{internalModel:internal,autoUpdate:false,destroy:()=>internal.destroy()} as any,{modelPath:'/Hiyori.model3.json'},sdk);
 try{
  p.advance(100);const head=c.getParameterValueById('ParamAngleX'),eye=c.getParameterValueById('ParamEyeBallX');
  p.setGazeSensitivity(2);p.advance(100);expect(c.getParameterValueById('ParamAngleX')).toBeCloseTo(head*2);expect(c.getParameterValueById('ParamEyeBallX')).toBeCloseTo(eye*2);
  p.setGazeSensitivity(0);p.advance(100);expect(c.getParameterValueById('ParamAngleX')).toBeCloseTo(0);expect(c.getParameterValueById('ParamEyeBallX')).toBeCloseTo(0);
  p.setGazeSensitivity(Infinity);p.advance(100);expect(c.getParameterValueById('ParamAngleX')).toBeCloseTo(head);
  p.setGazeSensitivity(100);p.advance(100);expect(c.getParameterValueById('ParamAngleX')).toBeCloseTo(head*16);expect(p.channel.elapsedMs).toBeCloseTo(500);
 }finally{p.dispose();}
});

it('amplifies speech mouth opening independently, saturates safely and closes on pause',()=>{
 const c=moc.createModel();const settings=new sdk.Cubism4ModelSettings({...JSON.parse(readFileSync('public/assets/live2d/hiyori/Hiyori.model3.json','utf8')),url:'http://localhost/Hiyori.model3.json'});
 const internal=new sdk.Cubism4InternalModel(c,settings,{motionPreload:'NONE'});
 const app={stage:{addChild:()=>{},removeChild:()=>{}},ticker:{add:()=>{},remove:()=>{}}};
 const p=new AvatarPlayer(app as any,{internalModel:internal,autoUpdate:false,destroy:()=>internal.destroy()} as any,{modelPath:'/Hiyori.model3.json'},sdk);
 const cue={profile_id:'',generation:0,config_revision:0,say_id:'lip',invoke_id:'i',call_id:'c'};
 const settle=()=>{for(let i=0;i<10;i++)p.advance(100);};
 try{
  p.accept({type:'start',cue,mode:'voice'});p.accept({type:'level',cue,level:0.1});settle();expect(c.getParameterValueById('ParamMouthOpenY')).toBeCloseTo(0.1);
  p.setLipSensitivity(4);settle();expect(c.getParameterValueById('ParamMouthOpenY')).toBeCloseTo(0.4);
  p.setGazeSensitivity(16);settle();expect(c.getParameterValueById('ParamMouthOpenY')).toBeCloseTo(0.4);
  p.setLipSensitivity(100);settle();expect(c.getParameterValueById('ParamMouthOpenY')).toBeCloseTo(1);
  p.setLipSensitivity(0);settle();expect(c.getParameterValueById('ParamMouthOpenY')).toBeCloseTo(0);
  p.setLipSensitivity(4);settle();p.accept({type:'pause'});settle();expect(c.getParameterValueById('ParamMouthOpenY')).toBe(0);
  p.accept({type:'resume'});p.accept({type:'level',cue,level:0.1});settle();expect(c.getParameterValueById('ParamMouthOpenY')).toBeCloseTo(0.4);
  p.accept({type:'reset'});settle();expect(c.getParameterValueById('ParamMouthOpenY')).toBeCloseTo(0);
 }finally{p.dispose();}
});

it.each([30,60,120])('turns toward the mouse at twice the original response rate at %i Hz without advancing other clocks',hz=>{
 const c=moc.createModel();const settings=new sdk.Cubism4ModelSettings({...JSON.parse(readFileSync('public/assets/live2d/hiyori/Hiyori.model3.json','utf8')),url:'http://localhost/Hiyori.model3.json'});
 const internal=new sdk.Cubism4InternalModel(c,settings,{motionPreload:'NONE'});
 const previous=new internal.focusController.constructor();previous.focus(-1,0,true);previous.focus(1,0);
 internal.focusController.focus(-1,0,true);internal.focusController.focus(1,0);
 const app={stage:{addChild:()=>{},removeChild:()=>{}},ticker:{add:()=>{},remove:()=>{}}};
 const p=new AvatarPlayer(app as any,{internalModel:internal,autoUpdate:false,destroy:()=>internal.destroy()} as any,{modelPath:'/Hiyori.model3.json'},sdk);
 try{
  for(let frame=0;frame<hz*0.4;frame++)p.advance(1000/hz);
  for(let step=0;step<48;step++)previous.update(1000/120);
  expect(internal.focusController.x).toBeGreaterThan(0.95);expect(previous.x).toBeLessThan(0.95);
  expect(c.getParameterValueById('ParamAngleX')).toBeCloseTo(3.5,1);expect(p.channel.elapsedMs).toBeCloseTo(400);
  internal.focusController.focus(-1,0);
  for(let frame=0;frame<hz*0.4;frame++){p.advance(1000/hz);expect(Math.abs(internal.focusController.x)).toBeLessThanOrEqual(1.01);}
  expect(internal.focusController.x).toBeLessThan(-0.95);expect(p.channel.elapsedMs).toBeCloseTo(800);
 }finally{p.dispose();}
});
