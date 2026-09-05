/** Exercises subscription lifetime with controlled HTTP and SSE, including delayed pages and bursts.
 * The tests protect cancelled subscriptions, serialized refreshes, pagination and cursor replay without
 * importing native storage or changing the Electron runtime used by desktop checks.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { TaskStatusFeed } from '../../../src/main/taskStatusFeed';
const feeds:TaskStatusFeed[]=[];
const deferred=<T>()=>{let resolve!:(value:T)=>void;let reject!:(error:Error)=>void;const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const item=(id:string)=>({id,title:`Task ${id}`,status:'running',prompt:'private'});
const page=(items:ReturnType<typeof item>[],nextCursor?:number)=>new Response(JSON.stringify({items,nextCursor}));
function events(signal:AbortSignal){
 let writer!:ReadableStreamDefaultController<Uint8Array>;
 const body=new ReadableStream<Uint8Array>({start(controller){writer=controller;}});
 const abort=()=>{try{writer.error(new Error('aborted'));}catch{/* Already closed. */}};
 signal.addEventListener('abort',abort,{once:true});
 return {response:new Response(body),send:(id:number)=>writer.enqueue(new TextEncoder().encode(`id: ${id}\nevent: task.status_changed\ndata: {}\n\n`)),close:()=>{signal.removeEventListener('abort',abort);writer.close();}};
}
const tick=()=>vi.advanceTimersByTimeAsync(0);
beforeEach(()=>vi.useFakeTimers());
afterEach(async()=>{feeds.splice(0).forEach(feed=>feed.stop());await tick();vi.useRealTimers();vi.unstubAllGlobals();});
function feed(changed=vi.fn(),profile=()=> 'profile'){const value=new TaskStatusFeed(profile,changed);feeds.push(value);return {value,changed};}

it('aborts old pages and cannot publish or request their next page after stop/restart',async()=>{
 const old=deferred<Response>();let oldSignal!:AbortSignal;let snapshots=0;
 vi.stubGlobal('fetch',vi.fn((url:string,init:RequestInit)=>{
   if(url.includes('/task-events'))return Promise.resolve(events(init.signal as AbortSignal).response);
   if(++snapshots===1){oldSignal=init.signal as AbortSignal;return old.promise;}
   return Promise.resolve(page([item('new')]));
 }));
 const {value,changed}=feed();value.start();await tick();value.stop();expect(oldSignal.aborted).toBe(true);
 value.start();await tick();old.resolve(page([item('old')],200));await tick();
 expect(snapshots).toBe(2);expect(changed.mock.calls.map(([state])=>state.tasks.map((task:any)=>task.id))).toEqual([['new']]);
});
it('never broadcasts a delayed event refresh error after unsubscribe',async()=>{
 const pending=deferred<Response>();let stream!:ReturnType<typeof events>;let snapshots=0;
 vi.stubGlobal('fetch',vi.fn((url:string,init:RequestInit)=>{
   if(url.includes('/task-events')){stream=events(init.signal as AbortSignal);return Promise.resolve(stream.response);}
   return ++snapshots===1?Promise.resolve(page([])):pending.promise;
 }));
 const {value,changed}=feed();value.start();await tick();stream.send(1);await vi.advanceTimersByTimeAsync(200);
 changed.mockClear();value.stop();pending.reject(new Error('late failure'));await tick();expect(changed).not.toHaveBeenCalled();
});
it('clears cancelled refresh timers so a new subscription can refresh',async()=>{
 const streams:ReturnType<typeof events>[]=[];let snapshots=0;
 vi.stubGlobal('fetch',vi.fn((url:string,init:RequestInit)=>{
   if(url.includes('/task-events')){const stream=events(init.signal as AbortSignal);streams.push(stream);return Promise.resolve(stream.response);}
   return Promise.resolve(page([item(String(++snapshots))]));
 }));
 const {value}=feed();value.start();await tick();streams[0].send(1);await tick();value.stop();value.start();await tick();streams[1].send(2);await vi.advanceTimersByTimeAsync(200);expect(snapshots).toBe(3);
});
it('coalesces event bursts during an in-flight refresh into one follow-up read',async()=>{
 let stream!:ReturnType<typeof events>;let snapshots=0;const pending=deferred<Response>();
 vi.stubGlobal('fetch',vi.fn((url:string,init:RequestInit)=>{
   if(url.includes('/task-events')){stream=events(init.signal as AbortSignal);return Promise.resolve(stream.response);}
   return ++snapshots===2?pending.promise:Promise.resolve(page([item(String(snapshots))]));
 }));
 const {value,changed}=feed();value.start();await tick();stream.send(1);await vi.advanceTimersByTimeAsync(200);stream.send(2);stream.send(3);await vi.advanceTimersByTimeAsync(200);expect(snapshots).toBe(2);
 pending.resolve(page([item('2')]));await tick();expect(snapshots).toBe(3);expect(changed.mock.calls.at(-1)?.[0].tasks).toEqual([{id:'3',title:'Task 3',status:'running'}]);
});
it('paginates one profile and retains only its newest 100 title/status rows',async()=>{
 const urls:string[]=[];vi.stubGlobal('fetch',vi.fn((url:string,init:RequestInit)=>{
   if(url.includes('/task-events'))return Promise.resolve(events(init.signal as AbortSignal).response);
   urls.push(url);return Promise.resolve(new URL(url).searchParams.has('cursor')?page(Array.from({length:50},(_,i)=>item(String(i+200)))):page(Array.from({length:200},(_,i)=>item(String(i))),200));
 }));
 const {value,changed}=feed();value.start();await tick();const tasks=changed.mock.calls.at(-1)?.[0].tasks;expect(tasks).toHaveLength(100);expect(tasks[0]).toEqual({id:'249',title:'Task 249',status:'running'});expect(tasks[99].id).toBe('150');expect(urls.every(url=>new URL(url).searchParams.get('ownerProfileId')==='profile')).toBe(true);
});
it('reconnects only one SSE stream and replays from its last event cursor',async()=>{
 const streams:ReturnType<typeof events>[]=[];const cursors:string[]=[];
 vi.stubGlobal('fetch',vi.fn((url:string,init:RequestInit)=>{
   if(url.includes('/task-events')){cursors.push((init.headers as Record<string,string>)['Last-Event-ID']);const stream=events(init.signal as AbortSignal);streams.push(stream);return Promise.resolve(stream.response);}
   return Promise.resolve(page([]));
 }));
 const {value}=feed();value.start();value.start();await tick();expect(streams).toHaveLength(1);streams[0].send(19);await tick();streams[0].close();await vi.advanceTimersByTimeAsync(1000);expect(cursors).toEqual(['0','19']);expect(streams).toHaveLength(2);
});

it('restarts paging for a changed profile without publishing or continuing old-profile pages',async()=>{
 const old=deferred<Response>();let profileId='old';const requested:string[]=[];
 vi.stubGlobal('fetch',vi.fn((url:string,init:RequestInit)=>{
   if(url.includes('/task-events'))return Promise.resolve(events(init.signal as AbortSignal).response);
   requested.push(new URL(url).searchParams.get('ownerProfileId')!);
   return requested.length===1?old.promise:Promise.resolve(page([item('new-profile-task')]));
 }));
 const {value,changed}=feed(vi.fn(),()=>profileId);value.start();await tick();profileId='new';old.resolve(page([item('old-profile-task')],200));await tick();
 expect(requested).toEqual(['old','new']);expect(changed).toHaveBeenCalledOnce();expect(changed.mock.calls[0][0].tasks[0].id).toBe('new-profile-task');
});
