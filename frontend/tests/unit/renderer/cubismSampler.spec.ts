/** Runs pinned Cubism Framework motion/expression evaluation against the real Hiyori Core model.
 * No renderer or curve evaluator is mocked; GPU drawing is separately covered by Electron E2E.
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
