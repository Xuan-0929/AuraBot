/** Exercises bounded debug collection, bounded diagnostics, and real rotating files.
 * Main's collector is tested with an injected HTTP boundary, including acoustic windows
 * surviving sanitization and disk writes, never a second voice connection. */
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { afterEach, expect, it, vi } from 'vitest';
import { LiveDebugService } from '../../../src/main/liveDebug';
import { appendDebugEvents, sanitizeDebugEvent, type LiveDebugEvent } from '../../../src/shared/contracts/liveDebug';
const roots:string[]=[];
const root=()=>{const path=mkdtempSync(join(tmpdir(),'aura-debug-'));roots.push(path);return path;};
afterEach(()=>{vi.useRealTimers();for(const path of roots.splice(0))rmSync(path,{recursive:true,force:true});});
const event=(seq:number,captureId='capture'):LiveDebugEvent=>({source:'pet',seq,instanceId:'pet-instance',captureId,timestamp:Date.now(),stage:'capture',name:'mic.stats',level:'info',profileId:'p',generation:1,details:{sent:seq}});

it('writes the backend-confirmed launch mode and fixture into each synchronized capture',async()=>{
 const service=new LiveDebugService(root(),async(_path,init)=>{
  const body=JSON.parse(String(init?.body));
  return {instanceId:'backend',captureId:body.captureId,cursor:1,events:[],dropped:0,enabled:true,
   state:{system:{debug:true,lockMemory:true,memoryFixture:'benchmark-memory-v1',api_key:'secret'}}};
 },()=>{},()=>{});
 try{
  service.setOnline(true);await service.setEnabled(true);await service.flush();
  expect(readFileSync(join(service.logDirectory,'live-debug.jsonl'),'utf8')).toContain('benchmark-memory-v1');
  expect(service.snapshot().events.find(event=>event.name==='launch.options')?.details).toEqual({debug:true,lockMemory:true,memoryFixture:'benchmark-memory-v1'});
 }finally{service.setOnline(false);await service.shutdown();}
});

it('preserves acoustic window and teardown facts from backend polling to JSONL',async()=>{
 const details={windowMs:501,windowFrames:25,vadPassFrames:22,rmsPassFrames:17,qualifiedFrames:16,
  maxQualifiedRunMs:160,rmsMin:.000031,rmsMax:.041234,thresholdMin:.02,thresholdMax:.023,
  maxConfirmMs:240,confirmMs:0,resetRmsCount:3,resetVadCount:1,resetBothCount:0,resetUnavailableCount:0,
  lastResetReason:'rms_below_threshold',lastResetFromMs:240,partial:false,vadStatus:'ready',vadReason:null,
  vadWarmupFrames:1,vadUnavailableFrames:0,vadErrorFrames:0,operation:'send_audio',finalizing:true,
  endpointPending:true,exceptionType:'RuntimeError'};
 let capture='';
 const service=new LiveDebugService(root(),async(path,init)=>{
  if(path.endsWith('/configure')){
   const body=JSON.parse(String(init?.body));capture=body.captureId;
   return {instanceId:'backend',captureId:capture,cursor:0,events:[],state:{},dropped:0,enabled:body.enabled};
  }
  return {instanceId:'backend',captureId:capture,cursor:1,events:[{...event(1,capture),source:'backend',instanceId:'backend',
   stage:'input',name:'acoustics',details:{...details,api_key:'secret',pcm:'private audio'}}],state:{},dropped:0,enabled:true};
 },()=>{},()=>{});
 service.setIdentity({profileId:'p',generation:1,configRevision:1});
 try{
  await service.setEnabled(true);service.setOnline(true);
  await vi.waitFor(()=>expect(service.snapshot().events.some(e=>e.name==='acoustics')).toBe(true));
  await service.flush();
  const lines=readFileSync(join(service.snapshot().logDirectory,'live-debug.jsonl'),'utf8').trim().split('\n').map(line=>JSON.parse(line));
  expect(lines.find(e=>e.name==='acoustics').details).toEqual(details);
 }finally{service.setOnline(false);await service.shutdown();}
});

it('bounds event count and bytes and drops unapproved data at the IPC boundary',()=>{
 const raw={...event(1),details:{sent:1,api_key:'secret',text:'你'.repeat(30000),deviceId:'device'}};
 const safe=sanitizeDebugEvent(raw)!;
 expect(safe.details.api_key).toBeUndefined();expect(safe.details.deviceId).toBeUndefined();expect(safe.truncated).toBe(true);
 expect(Buffer.byteLength(JSON.stringify(safe))).toBeLessThanOrEqual(32768);
 const events=appendDebugEvents([],Array.from({length:1400},(_,i)=>({...event(i),id:i})));
 expect(events).toHaveLength(1000);expect(events[0].seq).toBe(400);
 expect(sanitizeDebugEvent({...event(2),timestamp:NaN})).toBeNull();
});

it('keeps disk logging after subscribers close and clears only owned logs before accepting fresh capture',async()=>{
 const directory=root();const service=new LiveDebugService(directory,async()=>{throw Error('offline');},()=>{},()=>{});
 await service.setEnabled(true);const first=service.snapshot().captureId;
 service.recordPet(event(1,first));await service.flush();
 const logDir=service.snapshot().logDirectory;
 expect(readFileSync(join(logDir,'live-debug.jsonl'),'utf8')).toContain('mic.stats');
 await service.clear();service.recordPet(event(2,first));
 const next=service.snapshot().captureId;service.recordPet(event(3,next));await service.flush();
 const saved=readFileSync(join(logDir,'live-debug.jsonl'),'utf8');
 expect(saved).not.toContain('"seq":2');expect(saved).toContain('"seq":3');expect(service.snapshot().enabled).toBe(true);
 await service.setEnabled(false);service.recordPet(event(4,next));await service.shutdown();
 expect(readFileSync(join(logDir,'live-debug.jsonl'),'utf8')).not.toContain('"seq":4');
 expect(readdirSync(logDir)).toEqual(['live-debug.jsonl']);
});

it('does not overlap polls and ignores a response which finishes after clearing',async()=>{
 let release:((value:unknown)=>void)|undefined;let active=0,maxActive=0;
 const request=async(path:string,init?:RequestInit):Promise<any>=>{
  active++;maxActive=Math.max(maxActive,active);
  if(path.endsWith('/configure')||path.endsWith('/clear')){active--;const body=JSON.parse(String(init?.body));return {enabled:true,captureId:body.captureId,instanceId:'backend',cursor:0,events:[],dropped:0,state:{}};}
  try{return await new Promise(resolve=>{release=resolve;});}finally{active--;}
 };
 const service=new LiveDebugService(root(),request,()=>{},()=>{});
 await service.setEnabled(true);service.setOnline(true);
 await vi.waitFor(()=>expect(release).toBeTypeOf('function'));
 const old=service.snapshot().captureId;const clearing=service.clear();
 release!({enabled:true,captureId:old,instanceId:'backend',cursor:1,events:[{...event(1,old),source:'backend'}],dropped:0,state:{}});
 await clearing;expect(maxActive).toBe(1);expect(service.snapshot().events).toEqual([]);
 service.setOnline(false);await service.shutdown();
});

it('reconfigures a fresh backend before rejecting its empty capture identity',async()=>{
 let configured=0,capture='';
 const service=new LiveDebugService(root(),async(path,init)=>{
   if(path.endsWith('/configure')){configured++;capture=JSON.parse(String(init?.body)).captureId;return {instanceId:configured===1?'old':'new',captureId:capture,cursor:0,events:[],state:{},dropped:0,enabled:true};}
   return {instanceId:'new',captureId:configured===1?'':capture,cursor:0,events:[],state:{},dropped:0,enabled:true};
 },()=>{},()=>{});
 await service.setEnabled(true);service.setOnline(true);
 try{await vi.waitFor(()=>expect(configured).toBe(2),{timeout:1800});}finally{service.setOnline(false);await service.shutdown();}
});

it('retries a failed disable control without reading or logging more events',async()=>{
 let backendEnabled=false,failed=false,reads=0;
 const service=new LiveDebugService(root(),async(path,init)=>{
   const body=init?.body?JSON.parse(String(init.body)):{};
   if(path.endsWith('/configure')){
     if(!body.enabled&&!failed){failed=true;throw Error('temporary');}
     backendEnabled=body.enabled;
   }else reads++;
   return {instanceId:'backend',captureId:body.captureId,cursor:0,events:[],state:{},dropped:0,enabled:backendEnabled};
 },()=>{},()=>{});
 service.setOnline(true);await service.setEnabled(true);expect(backendEnabled).toBe(true);
 await service.setEnabled(false);const previousReads=reads;
 try{await vi.waitFor(()=>expect(backendEnabled).toBe(false),{timeout:1800});expect(reads).toBe(previousReads);}finally{service.setOnline(false);await service.shutdown();}
});

it('clearing before backend synchronization preserves the requested enabled state',async()=>{
 let backendEnabled=false;
 const service=new LiveDebugService(root(),async(path,init)=>{
   const body=init?.body?JSON.parse(String(init.body)):{};
   if(path.endsWith('/configure'))backendEnabled=body.enabled;
   return {instanceId:'backend',captureId:body.captureId,cursor:0,events:[],state:{},dropped:0,enabled:backendEnabled};
 },()=>{},()=>{});
 await service.setEnabled(true);service.setOnline(true);await service.clear();
 expect(backendEnabled).toBe(true);service.setOnline(false);await service.shutdown();
});

it('rotates exactly three owned files and keeps memory usable on a disk failure',async()=>{
 const directory=root(),logDir=join(directory,'logs','live-debug');mkdirSync(logDir,{recursive:true});
 writeFileSync(join(logDir,'live-debug.jsonl'),'x'.repeat(5*1024*1024));
 writeFileSync(join(logDir,'live-debug.jsonl.1'),'previous');writeFileSync(join(logDir,'live-debug.jsonl.2'),'expired');
 const service=new LiveDebugService(directory,async()=>{},()=>{},()=>{});
 await service.setEnabled(true);service.recordPet(event(1,service.capture().captureId));await service.flush();
 expect(readdirSync(logDir).sort()).toEqual(['live-debug.jsonl','live-debug.jsonl.1','live-debug.jsonl.2']);
 expect(readFileSync(join(logDir,'live-debug.jsonl.2'),'utf8')).toBe('previous');
 expect(statSync(join(logDir,'live-debug.jsonl')).size).toBeLessThanOrEqual(5*1024*1024);
 await service.shutdown();
 const failedRoot=root();writeFileSync(join(failedRoot,'logs'),'blocks directory');
 const failed=new LiveDebugService(failedRoot,async()=>{},()=>{},()=>{});
 await failed.setEnabled(true);failed.recordPet(event(1,failed.capture().captureId));await failed.flush();
 expect(failed.snapshot().error).toBe('debug_log_write_failed');expect(failed.snapshot().events.some(e=>e.name==='mic.stats')).toBe(true);
 await failed.shutdown();
});

it('joins an outstanding disabled control before shutdown issues another request',async()=>{
 let release:(()=>void)|undefined;let active=0,max=0;
 const service=new LiveDebugService(root(),async(_path,init)=>{
   active++;max=Math.max(max,active);
   const body=JSON.parse(String(init?.body));
   if(!release)await new Promise<void>(resolve=>{release=resolve;});
   active--;return {instanceId:'backend',captureId:body.captureId,cursor:0,events:[],state:{},dropped:0,enabled:body.enabled};
 },()=>{},()=>{});
 service.setOnline(true);await vi.waitFor(()=>expect(release).toBeTypeOf('function'));
 const shutdown=service.shutdown();await new Promise(resolve=>setTimeout(resolve,10));release!();await shutdown;
 expect(max).toBe(1);
});
