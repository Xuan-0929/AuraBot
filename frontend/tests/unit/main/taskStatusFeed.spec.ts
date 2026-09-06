/** Verifies profile-scoped snapshot SSE delivery and subscription cancellation with controlled streams.
 * Tests cover the public title/status projection, chunk parsing and reconnect behavior without REST reads.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { TaskStatusFeed } from '../../../src/main/taskStatusFeed';
const feeds:TaskStatusFeed[]=[];
const deferred=<T>()=>{let resolve!:(value:T)=>void;let reject!:(error:Error)=>void;const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const item=(id:string)=>({id,title:`Task ${id}`,status:'running',prompt:'private'});
function events(){
 let writer!:ReadableStreamDefaultController<Uint8Array>;
 const body=new ReadableStream<Uint8Array>({start(controller){writer=controller;}});
 return {response:new Response(body),raw:(text:string)=>writer.enqueue(new TextEncoder().encode(text)),send:(tasks:ReturnType<typeof item>[])=>writer.enqueue(new TextEncoder().encode(`event: task.snapshot\ndata: ${JSON.stringify({tasks})}\n\n`)),close:()=>writer.close()};
}
const tick=()=>vi.advanceTimersByTimeAsync(0);
beforeEach(()=>vi.useFakeTimers());
afterEach(async()=>{feeds.splice(0).forEach(feed=>feed.stop());await tick();vi.useRealTimers();vi.unstubAllGlobals();});
function feed(changed=vi.fn(),profile=()=> 'profile'){const value=new TaskStatusFeed(profile,changed);feeds.push(value);return {value,changed};}

it('uses one profile-scoped SSE stream and projects only the newest 100 title/status rows',async()=>{
 const stream=events();const fetcher=vi.fn().mockResolvedValue(stream.response);vi.stubGlobal('fetch',fetcher);
 const {value,changed}=feed();value.start();value.start();await tick();stream.send(Array.from({length:105},(_,i)=>item(String(105-i))));await tick();
 expect(fetcher).toHaveBeenCalledOnce();const [url,init]=fetcher.mock.calls[0];expect(new URL(url).pathname).toBe('/api/v1/task-events');expect(new URL(url).searchParams.get('ownerProfileId')).toBe('profile');expect(init.headers?.['Last-Event-ID']).toBeUndefined();
 expect(changed.mock.calls[0][0].tasks).toHaveLength(100);expect(changed.mock.calls[0][0].tasks[0]).toEqual({id:'105',title:'Task 105',status:'running'});
});
it('parses split CRLF events, ignores other event kinds and replaces the complete snapshot',async()=>{
 const stream=events();vi.stubGlobal('fetch',vi.fn().mockResolvedValue(stream.response));const {value,changed}=feed();value.start();await tick();
 stream.raw('event: task.created\ndata: {}\n\nevent: task.snap');stream.raw('shot\r\ndata: {"tasks":[{"id":"a","title":"你好","status":"done"}]}\r\n\r\n');await tick();
 expect(changed).toHaveBeenLastCalledWith({tasks:[{id:'a',title:'你好',status:'done'}]});stream.send([]);await tick();expect(changed).toHaveBeenLastCalledWith({tasks:[]});
});
it('aborts a stopped request and never publishes its late response after restart',async()=>{
 const pending=deferred<Response>();const stream=events();const fetcher=vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValue(stream.response);vi.stubGlobal('fetch',fetcher);
 const {value,changed}=feed();value.start();await tick();const signal=fetcher.mock.calls[0][1].signal;value.stop();expect(signal.aborted).toBe(true);value.start();await tick();stream.send([item('new')]);const old=events();old.send([item('old')]);pending.resolve(old.response);await tick();
 expect(changed).toHaveBeenCalledOnce();expect(changed.mock.calls[0][0].tasks[0].id).toBe('new');
});
it('never publishes delayed errors or reconnects after unsubscribe',async()=>{
 const pending=deferred<Response>();const fetcher=vi.fn().mockReturnValue(pending.promise);vi.stubGlobal('fetch',fetcher);const {value,changed}=feed();value.start();value.stop();pending.reject(new Error('late'));await vi.advanceTimersByTimeAsync(10000);expect(changed).not.toHaveBeenCalled();expect(fetcher).toHaveBeenCalledOnce();
});
it('reconnects to a fresh snapshot without cursor replay and cancels pending retries',async()=>{
 const streams=[events(),events()];const fetcher=vi.fn().mockResolvedValueOnce(streams[0].response).mockResolvedValueOnce(streams[1].response);vi.stubGlobal('fetch',fetcher);
 const {value,changed}=feed();value.start();await tick();streams[0].send([item('first')]);await tick();streams[0].close();await vi.advanceTimersByTimeAsync(1000);expect(fetcher).toHaveBeenCalledTimes(2);streams[1].send([item('fresh')]);await tick();expect(changed.mock.calls.at(-1)?.[0].tasks[0].id).toBe('fresh');streams[1].close();await tick();value.stop();await vi.advanceTimersByTimeAsync(10000);expect(fetcher).toHaveBeenCalledTimes(2);
});
it('discards an old-profile snapshot and reconnects for the current profile',async()=>{
 const streams=[events(),events()];let profileId='old';const fetcher=vi.fn().mockResolvedValueOnce(streams[0].response).mockResolvedValueOnce(streams[1].response);vi.stubGlobal('fetch',fetcher);
 const {value,changed}=feed(vi.fn(),()=>profileId);value.start();await tick();profileId='new';streams[0].send([item('old')]);await vi.advanceTimersByTimeAsync(1000);expect(changed).not.toHaveBeenCalled();expect(new URL(fetcher.mock.calls[1][0]).searchParams.get('ownerProfileId')).toBe('new');streams[1].send([item('new')]);await tick();expect(changed.mock.calls[0][0].tasks[0].id).toBe('new');
});
it('rejects malformed snapshot rows and recovers on the next connection',async()=>{
 const streams=[events(),events()];vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(streams[0].response).mockResolvedValueOnce(streams[1].response));const {value,changed}=feed();value.start();await tick();streams[0].raw('event: task.snapshot\ndata: {"tasks":[{"id":"a","title":7,"status":"done"}]}\n\n');await tick();expect(changed.mock.calls[0][0].error).toBeTruthy();await vi.advanceTimersByTimeAsync(1000);streams[1].send([item('valid')]);await tick();expect(changed).toHaveBeenLastCalledWith({tasks:[{id:'valid',title:'Task valid',status:'running'}]});
});
