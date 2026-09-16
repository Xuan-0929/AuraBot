/** Exercises the debug renderer through its desktop IPC seam, including revision races,
 * bounded event pages, filters and cleanup. The App route test prevents debug windows from
 * initializing chat history, Pet media or ordinary event subscriptions. */
// @vitest-environment jsdom
import { createApp, nextTick } from 'vue';
import { afterEach, expect, it, vi } from 'vitest';
import LiveDebugWindow from '../../../src/features/live-debug/LiveDebugWindow.vue';
import App from '../../../src/App.vue';
const store=vi.hoisted(()=>({initialize:vi.fn(),subscribeToPetEvents:vi.fn()}));
vi.mock('../../../src/app/store',()=>({useAuraBotStore:()=>store}));
vi.mock('../../../src/features/pet-vue/PetApp.vue',()=>({default:{template:'<div data-pet />'}}));
vi.mock('../../../src/features/settings/SettingsWindow.vue',()=>({default:{template:'<div />'}}));
vi.mock('../../../src/features/console/ConsoleWindow.vue',()=>({default:{template:'<div />'}}));
let dispose=()=>{};
afterEach(()=>{dispose();window.location.hash='';vi.clearAllMocks();});
const event=(id:number,extra={})=>({id,source:'backend',seq:id,instanceId:'backend',captureId:'capture',timestamp:1700000000000+id,stage:'stt',name:`event-${id}`,level:'info',details:{text:`transcript-${id}`},...extra});
const snapshot=(revision=1,events:any[]=[event(1)])=>({revision,events,enabled:true,captureId:'capture',state:{stt:{stablePrefix:'stable words',mutableSuffix:'mutable words'},playback:{text:'reply words'}},dropped:0,logDirectory:'/tmp/debug',updatedAt:0});
function mount(component=LiveDebugWindow,overrides:Record<string,unknown>={}){
 let emit:(value:any)=>void=()=>{};const unsubscribe=vi.fn();
 const api={getSettings:vi.fn(async()=>({config:{desktop:{pet:{displayLanguage:'en'}}}})),getLiveDebugState:vi.fn(async()=>snapshot()),onLiveDebugChanged:(listener:typeof emit)=>{emit=listener;return unsubscribe;},clearLiveDebugLogs:vi.fn(async()=>snapshot(9,[])),openLiveDebugLogDirectory:vi.fn(async()=>{}),...overrides};
 Object.assign(window,{aurabot:api});const host=document.createElement('div');document.body.append(host);const app=createApp(component);app.mount(host);dispose=()=>{app.unmount();host.remove();};
 return {host,api,emit:(value:any)=>emit(value),unsubscribe};
}
it('keeps newest 200 visible with older pages, filters and expandable structured details',async()=>{
 const {host,emit}=mount(LiveDebugWindow,{getLiveDebugState:async()=>snapshot(1,Array.from({length:450},(_,i)=>event(i+1)))});
 await vi.waitFor(()=>expect(host.querySelectorAll('[data-debug-event]')).toHaveLength(200));
 expect(host.textContent).toContain('stable words');expect(host.querySelector('.transcripts article:last-child p')?.textContent).toBe('reply words');
 const button=(text:string)=>[...host.querySelectorAll('button')].find(item=>item.textContent===text)!;
 button('Older').click();await nextTick();expect(host.querySelector('[data-debug-event]')?.textContent).toContain('event-51');
 button('Latest').click();await nextTick();
 const search=host.querySelector<HTMLInputElement>('[name="debug-search"]')!;search.value='transcript-450';search.dispatchEvent(new Event('input'));await nextTick();expect(host.querySelectorAll('[data-debug-event]')).toHaveLength(1);
 search.value='';search.dispatchEvent(new Event('input'));await nextTick();
 emit({...snapshot(2,[event(451,{level:'error',source:'pet',stage:'capture'})]),reset:false});await nextTick();
 const level=host.querySelector<HTMLSelectElement>('[name="debug-level"]')!;level.value='error';level.dispatchEvent(new Event('change'));await nextTick();expect(host.querySelectorAll('[data-debug-event]')).toHaveLength(1);
 expect(host.querySelector('[data-debug-event] details')?.textContent).toContain('capture');
 button('Clear debug logs').click();await vi.waitFor(()=>expect(host.querySelectorAll('[data-debug-event]')).toHaveLength(0));
 dispose();expect(host.isConnected).toBe(false);
});
it('merges updates received during initial snapshot and rejects stale/reset races',async()=>{
 let resolve!:(value:any)=>void;const initial=new Promise(resolveState=>{resolve=resolveState;});
 const {host,emit,unsubscribe}=mount(LiveDebugWindow,{getLiveDebugState:()=>initial});
 emit({...snapshot(2,[event(2)]),reset:false});resolve(snapshot(1));
 await vi.waitFor(()=>expect(host.querySelectorAll('[data-debug-event]')).toHaveLength(2));
 emit({...snapshot(3,[]),reset:true});emit({...snapshot(2,[event(2)]),reset:false});await nextTick();expect(host.querySelectorAll('[data-debug-event]')).toHaveLength(0);
 emit({...snapshot(4,[event(3),event(3)]),reset:false});await nextTick();expect(host.querySelectorAll('[data-debug-event]')).toHaveLength(1);
 const auto=host.querySelector<HTMLInputElement>('[name="debug-autoscroll"]')!;auto.click();emit({...snapshot(5,[event(4)]),reset:false});await nextTick();expect(host.querySelectorAll('[data-debug-event]')).toHaveLength(2);
 dispose();expect(unsubscribe).toHaveBeenCalledOnce();
});
it('opens the debug route without chat initialization or a Pet mount',async()=>{
 window.location.hash='#live-debug';const {host}=mount(App as typeof LiveDebugWindow);
 await vi.waitFor(()=>expect(host.querySelector('[data-live-debug]')).not.toBeNull());
 expect(host.querySelector('[data-pet]')).toBeNull();expect(store.initialize).not.toHaveBeenCalled();expect(store.subscribeToPetEvents).not.toHaveBeenCalled();
});
it('caps retained history at 1000 while source and stage filters reach older events',async()=>{
 const {host,emit}=mount(LiveDebugWindow,{getLiveDebugState:async()=>snapshot(1,Array.from({length:1100},(_,i)=>event(i+1,{source:i===100?'pet':'backend',stage:i===100?'capture':'stt'})))});
 await vi.waitFor(()=>expect(host.querySelectorAll('[data-debug-event]')).toHaveLength(200));
 expect(host.querySelector('.event-heading')?.textContent).toContain('1000 / 1000');
 const select=async(name:string,value:string)=>{const element=host.querySelector<HTMLSelectElement>(`[name="${name}"]`)!;element.value=value;element.dispatchEvent(new Event('change'));await nextTick();};
 await select('debug-source','pet');await select('debug-stage','capture');expect(host.querySelectorAll('[data-debug-event]')).toHaveLength(1);expect(host.querySelector('[data-debug-event]')?.textContent).toContain('event-101');
 emit({...snapshot(2,Array.from({length:100},(_,i)=>event(1101+i))),reset:false});await nextTick();expect(host.querySelectorAll('[data-debug-event]')).toHaveLength(0);
});
it('does not restore an old initial snapshot after a reset and shows operation failures',async()=>{
 let resolve!:(value:any)=>void;const initial=new Promise(resolveState=>{resolve=resolveState;});
 const {host,emit}=mount(LiveDebugWindow,{getLiveDebugState:()=>initial,openLiveDebugLogDirectory:async()=>{throw new Error('folder unavailable');}});
 emit({...snapshot(3,[]),reset:true});resolve(snapshot(1));await nextTick();await nextTick();expect(host.querySelectorAll('[data-debug-event]')).toHaveLength(0);
 const folder=[...host.querySelectorAll('button')].find(item=>item.textContent==='Open log directory')!;folder.click();await vi.waitFor(()=>expect(host.querySelector('[role=alert]')?.textContent).toContain('folder unavailable'));
});
