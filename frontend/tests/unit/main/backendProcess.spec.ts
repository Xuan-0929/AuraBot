/** Exercises Main's Backend ownership with process/HTTP doubles.
 * Readiness notices remain informational, stdin EOF requests graceful shutdown, and Windows
 * timeout recovery targets only the owned process tree rather than terminating a wrapper alone.
 * Source startup must use the prepared environment without synchronizing dependencies. */
import { EventEmitter } from 'node:events';
import { mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { afterEach, expect, it, vi } from 'vitest';
import { DesktopConfigStore } from '../../../src/main/desktopConfigStore';
import { BackendProcess } from '../../../src/main/backendProcess';

const doubles=vi.hoisted(()=>({spawn:vi.fn()}));
vi.mock('node:child_process',()=>({spawn:doubles.spawn}));
vi.mock('node:net',()=>({createServer:()=>({once(){return this;},listen(_port:number,_host:string,ready:()=>void){ready();},close(done:()=>void){done();}})}));
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();vi.clearAllMocks();});

it.each([false,true])('uses pipe shutdown and only escalates a stuck owned Windows child (%s)',async(stuck)=>{
 const root=mkdtempSync(join(tmpdir(),'aura-backend-'));const config=new DesktopConfigStore(root);
 vi.stubGlobal('process',{...process,platform:'win32'});
 const child=Object.assign(new EventEmitter(),{pid:12345,exitCode:null as number|null,signalCode:null,kill:vi.fn(),stdin:{on:vi.fn(),end:vi.fn()}});
 const exit=()=>{child.exitCode=0;child.emit('exit',0);};
 child.stdin.end.mockImplementation(()=>{if(!stuck)queueMicrotask(exit);});
 doubles.spawn.mockImplementation((command,args,options)=>{
   if(command==='taskkill'){expect(args).toEqual(['/PID','12345','/T','/F']);queueMicrotask(exit);return new EventEmitter();}
   expect(args).toContain('backend.desktop_runner');expect(args).toContain('--no-sync');expect(options.stdio).toEqual(['pipe','ignore','ignore']);
   vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({ready:true,instanceId:options.env.AURABOT_BACKEND_INSTANCE,configRevision:0,launchOptions:{debug:false,lockMemory:false},capabilities:{text:true,voice:true,tasks:false},taskNotice:'git_bash_missing'})})));
   return child;
 });
 try {
   const backend=new BackendProcess(config,root,()=>{});
   await backend.start();expect(config.online).toBe(true);expect(config.error).toBeUndefined();expect(config.status().taskNotice).toBe('git_bash_missing');
   vi.useFakeTimers();const stopping=backend.stop();await vi.advanceTimersByTimeAsync(stuck?40001:1);await stopping;
   expect(child.stdin.end).toHaveBeenCalledOnce();expect(child.kill).not.toHaveBeenCalled();
   expect(doubles.spawn).toHaveBeenCalledTimes(stuck?2:1);expect(config.online).toBe(false);
 } finally {rmSync(root,{recursive:true,force:true});}
});

it('rechecks missing Bash on Apply even when saved configuration has not changed',async()=>{
 const root=mkdtempSync(join(tmpdir(),'aura-bash-retry-'));const config=new DesktopConfigStore(root);
 let starts=0;let identity='';const pipes:Array<ReturnType<typeof vi.fn>>=[];
 doubles.spawn.mockImplementation((_command,_args,options)=>{
   starts++;identity=options.env.AURABOT_BACKEND_INSTANCE;
   const child=Object.assign(new EventEmitter(),{pid:12345,exitCode:null as number|null,signalCode:null,stdin:{on:vi.fn(),end:vi.fn()}});
   child.stdin.end.mockImplementation(()=>queueMicrotask(()=>{child.exitCode=0;child.emit('exit',0);}));pipes.push(child.stdin.end);
   return child;
 });
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({ready:true,instanceId:identity,configRevision:0,launchOptions:{debug:false,lockMemory:false},capabilities:{text:true,voice:true,tasks:starts>1},taskNotice:starts===1?'git_bash_missing':null})})));
 const backend=new BackendProcess(config,root,()=>{});
 try{
   await backend.start();expect(config.status().backendPending).toBe(false);
   await backend.apply();expect(starts).toBe(2);expect(pipes[0]).toHaveBeenCalledOnce();
   expect(config.online).toBe(true);expect(config.status().taskNotice).toBeUndefined();expect(config.capabilities?.tasks).toBe(true);
 }finally{await backend.stop();rmSync(root,{recursive:true,force:true});}
});
