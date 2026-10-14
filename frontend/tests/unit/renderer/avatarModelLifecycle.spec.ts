// @vitest-environment jsdom
/** Mounts the real Vue Model component to exercise watcher wiring and asynchronous ownership.
 * Framing adjusts placement only; gaze and speech-mouth edits forward independently without recreating models or reconfiguring playback.
 * Only the SDK factory is deferred; metadata-only edits are ignored while actual Vue lifecycle/configuration forwarding remains production code.
 */
import {afterEach,expect,it,vi} from 'vitest';
import {createApp,h,markRaw,nextTick,onUnmounted,reactive,shallowRef,toRaw} from 'vue';
import Model from '../../../src/features/pet-vue/live2d/Model.vue';
import {emptyAvatarAdaptor} from '../../../src/shared/contracts/avatar';
const {factory}=vi.hoisted(()=>({factory:vi.fn()}));
vi.mock('../../../src/features/live2d/player',()=>({createAvatarPlayer:factory,avatarInstances:{active:0,created:0,destroyed:0}}));
vi.mock('../../../src/features/live2d/runtime',()=>({renderLive2DFrame:vi.fn(),normalizeLive2DHitAreaNames:()=>[]}));
function deferred<T>(){let resolve!:(value:T)=>void;let reject!:(error:Error)=>void;const promise=new Promise<T>((finish,fail)=>{resolve=finish;reject=fail;});return {promise,resolve,reject};}
function fake(){return {model:{anchor:{set:vi.fn()},internalModel:{width:200,height:300},scale:{set:vi.fn()},focus:vi.fn(),hitTest:vi.fn(),x:0,y:0},configure:vi.fn(async():Promise<void>=>undefined),prepare:vi.fn(async()=>undefined),setGeneration:vi.fn(),setGazeSensitivity:vi.fn(),setLipSensitivity:vi.fn(),dispose:vi.fn(),load:vi.fn(),diagnostics:{starts:0,dropped:0},channel:{elapsedMs:0}};}
let cleanup=()=>{};
afterEach(()=>{cleanup();cleanup=()=>{};factory.mockReset();vi.useRealTimers();});
function mount(onCanvasDestroy?:()=>void){
 const props=reactive({app:markRaw({render:vi.fn()}),modelPath:'/same.model3.json',profileId:'a',revision:1,generation:1,modelVersion:'v',adaptor:emptyAvatarAdaptor(),focusAt:{x:0,y:0},canvasSize:{width:300,height:400},viewTransform:{scale:1,offsetX:0,offsetY:0}});
 const reference=shallowRef<any>();const host=document.createElement('div');document.body.append(host);
 const Canvas={setup(){onUnmounted(()=>onCanvasDestroy?.());return()=>h('canvas');}};
 const app=createApp({setup:()=>()=>h('div',[h(Canvas),h(Model,{...props,ref:reference} as any)])});app.mount(host);
 let closed=false;const unmount=()=>{if(closed)return;closed=true;app.unmount();host.remove();};cleanup=unmount;
 return {props,host,reference,unmount};
}
async function flush(){await nextTick();await Promise.resolve();await nextTick();}
it('reconciles latest revision and generation after a deferred initial model load',async()=>{
 const load=deferred<ReturnType<typeof fake>>();factory.mockReturnValue(load.promise);const {props,host}=mount();
 props.revision=2;props.generation=9;props.adaptor.states.idle=['new_idle'];await flush();
 const player=fake();load.resolve(player);await vi.waitFor(()=>expect(host.querySelector('[data-avatar-state]')?.getAttribute('data-avatar-state')).toBe('ready'));
 expect(player.configure).toHaveBeenCalledWith(expect.objectContaining({revision:2,generation:9,adaptor:expect.objectContaining({states:expect.objectContaining({idle:['new_idle']})})}));
 expect(player.setGeneration).toHaveBeenLastCalledWith(9);expect(factory).toHaveBeenCalledOnce();
});
it('keeps the resident model across content-equal store refresh and forwards nested draft edits',async()=>{
 const player=fake();factory.mockResolvedValue(player);const {props,host,unmount}=mount();await vi.waitFor(()=>expect(host.querySelector('[data-avatar-state]')?.getAttribute('data-avatar-state')).toBe('ready'));
 props.adaptor=JSON.parse(JSON.stringify(toRaw(props.adaptor)));await flush();expect(factory).toHaveBeenCalledOnce();expect(player.dispose).not.toHaveBeenCalled();
 props.adaptor.entries.nod={kind:'motion',source:{file:'model/nod.motion3.json'},description:'原始说明',agent_enabled:false,playback:{mode:'once'}};await flush();
 player.configure.mockClear();props.adaptor.entries.nod.description='嵌套草稿更改';props.adaptor.entries.nod.display_name='新名称';await flush();expect(player.configure).not.toHaveBeenCalled();props.adaptor.entries.nod.playback.fade_in_ms=100;await flush();expect(player.configure).toHaveBeenCalledOnce();
 expect(factory).toHaveBeenCalledOnce();unmount();expect(player.dispose).toHaveBeenCalledOnce();
});
it('releases a late factory result once when the component has already unmounted',async()=>{
 const load=deferred<ReturnType<typeof fake>>();factory.mockReturnValue(load.promise);const {unmount}=mount();unmount();const player=fake();load.resolve(player);await flush();
 expect(player.dispose).toHaveBeenCalledOnce();expect(player.configure).not.toHaveBeenCalled();expect(player.prepare).not.toHaveBeenCalled();
});
it('does not continue preparing a disposed old player when a newer model replaces it',async()=>{
 const old=fake(),next=fake(),configuration=deferred<void>();old.configure.mockImplementation(()=>configuration.promise);factory.mockResolvedValueOnce(old).mockResolvedValueOnce(next);const {props,host}=mount();await vi.waitFor(()=>expect(old.configure).toHaveBeenCalled());
 props.modelPath='/replacement.model3.json';await vi.waitFor(()=>expect(next.prepare).toHaveBeenCalled());configuration.resolve();await flush();
 expect(old.dispose).toHaveBeenCalledOnce();expect(old.prepare).not.toHaveBeenCalled();expect(old.setGeneration).not.toHaveBeenCalled();expect(host.querySelector('[data-avatar-state]')?.getAttribute('data-avatar-state')).toBe('ready');
});
it('settles readiness and clears its timer immediately on unmount',async()=>{
 vi.useFakeTimers();const load=deferred<ReturnType<typeof fake>>();factory.mockReturnValue(load.promise);const {reference,unmount}=mount();const resolved=vi.fn();reference.value.ready().then(resolved);
 unmount();await flush();expect(resolved).toHaveBeenCalledWith(expect.objectContaining({available:false}));expect(vi.getTimerCount()).toBe(0);
 const player=fake();load.resolve(player);await flush();expect(player.dispose).toHaveBeenCalledOnce();
});

it('does not publish an old configuration failure after a newer revision is ready',async()=>{
 const player=fake(),old=deferred<void>();player.configure.mockImplementationOnce(()=>old.promise).mockResolvedValue(undefined);factory.mockResolvedValue(player);const {props,host}=mount();
 await vi.waitFor(()=>expect(player.configure).toHaveBeenCalledOnce());props.revision=2;await vi.waitFor(()=>expect(player.configure).toHaveBeenCalledTimes(2));await flush();
 expect(host.querySelector('[data-avatar-state]')?.getAttribute('data-avatar-state')).toBe('ready');old.reject(new Error('old configuration failed'));await flush();
 expect(host.querySelector('[data-avatar-state]')?.getAttribute('data-avatar-state')).toBe('ready');
});

it('disposes player before its sibling Canvas destroys the shared application ticker',async()=>{
 const order:string[]=[];const player=fake();player.dispose.mockImplementation(()=>{order.push('model');});factory.mockResolvedValue(player);
 const {unmount}=mount(()=>order.push('canvas'));await vi.waitFor(()=>expect(player.prepare).toHaveBeenCalled());unmount();
 expect(order).toEqual(['model','canvas']);
});

it('forwards gaze drafts without reloading or reconfiguring the player',async()=>{
 const player=fake();factory.mockResolvedValue(player);const {props}=mount();await vi.waitFor(()=>expect(player.prepare).toHaveBeenCalledOnce());
 props.adaptor.gaze_sensitivity=2.5;await flush();expect(player.setGazeSensitivity).toHaveBeenLastCalledWith(2.5);expect(factory).toHaveBeenCalledOnce();expect(player.configure).toHaveBeenCalledOnce();expect(player.prepare).toHaveBeenCalledOnce();
});

it('forwards speech-mouth drafts without restarting playback',async()=>{
 const player=fake();factory.mockResolvedValue(player);const {props}=mount();await vi.waitFor(()=>expect(player.prepare).toHaveBeenCalledOnce());
 props.adaptor.lip_sensitivity=4;await flush();expect(player.setLipSensitivity).toHaveBeenLastCalledWith(4);expect(factory).toHaveBeenCalledOnce();expect(player.configure).toHaveBeenCalledOnce();expect(player.prepare).toHaveBeenCalledOnce();
});

it('switches framing by resizing the same player without restarting playback',async()=>{
 const player=fake();factory.mockResolvedValue(player);const {props}=mount();await vi.waitFor(()=>expect(player.prepare).toHaveBeenCalledOnce());
 const fullScale=player.model.scale.set.mock.lastCall![0];
 props.adaptor.show_full_model=false;await flush();expect(player.model.scale.set.mock.lastCall![0]).toBeGreaterThan(fullScale);
 props.adaptor.show_full_model=true;await flush();expect(player.model.scale.set.mock.lastCall![0]).toBeCloseTo(fullScale);
 expect(factory).toHaveBeenCalledOnce();expect(player.configure).toHaveBeenCalledOnce();expect(player.prepare).toHaveBeenCalledOnce();
});
