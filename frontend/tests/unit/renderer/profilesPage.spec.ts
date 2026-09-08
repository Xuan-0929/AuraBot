/** Mounts the real Profile editor and its event wiring with controlled repository IPC.
 * The player boundary is replaced here only to inspect editor commands; separate runtime tests
 * exercise Cubism. These tests prove editing B does not activate B and drafts survive navigation. */
// @vitest-environment jsdom
import {createApp,nextTick,defineComponent,h} from 'vue';
import {afterEach,expect,it,vi} from 'vitest';
import {createDefaultProfile} from '../../../src/core/profiles/soulService';
import {emptyAvatarAdaptor} from '../../../src/shared/contracts/avatar';
import type {ProfileDetail} from '../../../src/shared/contracts/profiles';
const play=vi.hoisted(()=>vi.fn());
vi.mock('../../../src/features/live2d/Live2DPreview.vue',()=>({default:defineComponent({props:['profileId','modelVersion'],setup(props,ctx){ctx.expose({play,stop:vi.fn(),setState:vi.fn(),validate:async(file:string,kind:string)=>ctx.emit('validated',{file,kind,profileId:props.profileId,modelVersion:props.modelVersion})});return()=>h('div',{'data-test':'preview'},'独立播放器');}})}));
import ProfilesPage from '../../../src/features/profiles/ProfilesPage.vue';
let dispose=()=>{};
afterEach(()=>{dispose();vi.restoreAllMocks();});
const copy=<T,>(value:T):T=>JSON.parse(JSON.stringify(value));
function fixture():ProfileDetail{const seed=createDefaultProfile();const profile={...seed.profile,id:'b',active:false,displayName:'B',soul:{...seed.soul,profileId:'b'}};return {saved:{profile,revision:1,adaptor:emptyAvatarAdaptor(),modelVersion:''},applied:null,selected:false,memory:{directory:'/temporary/b/memory',initialized:false},resources:[]};}
function mount(detail:ProfileDetail){let current=copy(detail);const runtime={selectedId:'a',activeId:'a',revision:1,generation:1,online:true};const activateProfile=vi.fn(async()=>({runtime}));const saveProfile=vi.fn(async(_id,input)=>{current={...current,saved:{...current.saved,...copy(input),revision:current.saved.revision+1}};return copy(current);});const api={listProfiles:async()=>({runtime,profiles:[{id:'a',displayName:'A',selected:true,revision:1,appliedRevision:1,modelName:null,visualStatus:'unconfigured'},{id:'b',displayName:'B',selected:false,revision:1,appliedRevision:null,modelName:null,visualStatus:'unconfigured'}]}),getProfile:async()=>copy(current),onProfilesChanged:()=>()=>{},activateProfile,saveProfile,verifyProfileResources:vi.fn(async(_id,_version,files)=>{current.resources=current.resources.map(r=>({...r,verified:files.includes(r.file)||r.verified}));return copy(current);})};Object.assign(window,{aurabot:api});const host=document.createElement('div');document.body.append(host);const app=createApp(ProfilesPage);const instance=app.mount(host) as unknown as {canLeave:()=>Promise<boolean>};dispose=()=>{app.unmount();host.remove();};return {host,api,instance};}
function button(host:HTMLElement,text:string){return [...host.querySelectorAll('button')].find(b=>b.textContent?.trim()===text)!;}
async function openB(host:HTMLElement){await vi.waitFor(()=>expect(host.textContent).toContain('B'));const cards=host.querySelectorAll('article');(cards[1]!.querySelector('button') as HTMLButtonElement).click();await vi.waitFor(()=>expect(host.textContent).toContain('正在编辑：B'));}
it('edits full Soul and saves B without activating, preserving a dirty draft until user decides',async()=>{const {host,api,instance}=mount(fixture());await openB(host);const name=host.querySelector('input')!;name.value='B renamed';name.dispatchEvent(new Event('input',{bubbles:true}));await nextTick();expect(host.textContent).toContain('有未保存修改');const leave=instance.canLeave();await nextTick();expect(host.querySelector('[role=alertdialog]')).not.toBeNull();button(host,'继续编辑').click();expect(await leave).toBe(false);button(host,'保存').click();await vi.waitFor(()=>expect(api.saveProfile).toHaveBeenCalledOnce());expect(api.saveProfile.mock.calls[0]![0]).toBe('b');expect(api.saveProfile.mock.calls[0]![1].profile.soul).toHaveProperty('speakingStyle');expect(api.activateProfile).not.toHaveBeenCalled();expect(host.textContent).toContain('/temporary/b/memory');});
it('previews an unlabelled resource then adds a disabled semantic entry',async()=>{const detail=fixture();detail.saved.modelVersion='v1';detail.saved.adaptor.model={manifest:'model/Test.model3.json'};detail.resources=[{file:'model/001.motion3.json',kind:'motion',referenced:true,verified:false}];const {host,api}=mount(detail);await openB(host);const checkbox=[...host.querySelectorAll('input[type=checkbox]')][0] as HTMLInputElement;checkbox.click();await nextTick();button(host,'预览 / 验证').click();await vi.waitFor(()=>expect(api.verifyProfileResources).toHaveBeenCalledWith('b','v1',['model/001.motion3.json']));expect(play).toHaveBeenCalledWith({motion:'model/001.motion3.json',loop:false});await nextTick();button(host,'添加语义标记').click();await nextTick();expect(host.querySelector('.profile-entry')).not.toBeNull();button(host,'保存').click();await vi.waitFor(()=>expect(api.saveProfile).toHaveBeenCalledOnce());const entry=api.saveProfile.mock.calls[0]![1].adaptor.entries.entry_1;expect(entry.agent_enabled).toBe(false);expect(entry.source.file).toBe('model/001.motion3.json');expect(api.activateProfile).not.toHaveBeenCalled();});

it('keeps preview opt-in and opens advanced transitions after failed save',async()=>{
 const detail=fixture();detail.saved.modelVersion='v1';detail.saved.adaptor.model={manifest:'model/Test.model3.json'};
 detail.saved.adaptor.entries.wave={kind:'motion',source:{file:'wave.motion3.json'},description:'Wave',agent_enabled:false,playback:{mode:'once',fade_in_ms:200}};
 const {host,api}=mount(detail);await openB(host);
 expect(host.querySelector('[data-test=preview]')).toBeNull();
 const diagnostics=host.querySelector('details.profile-preview-diagnostics') as HTMLDetailsElement;expect(diagnostics).not.toBeNull();expect(diagnostics.open).toBe(false);
 const advanced=host.querySelector('.profile-entry details') as HTMLDetailsElement;expect(advanced.open).toBe(false);
 api.saveProfile.mockRejectedValueOnce(new Error('无法保存过渡配置'));
 const name=host.querySelector('input')!;name.value='Updated B';name.dispatchEvent(new Event('input',{bubbles:true}));await nextTick();button(host,'保存').click();
 await vi.waitFor(()=>expect(host.querySelector('[role=alert]')?.textContent).toContain('无法保存过渡配置'));
 expect(advanced.open).toBe(true);expect(host.querySelector('[data-test=preview]')).toBeNull();
});

it('keeps the discard decision keyboard accessible and returns focus when cancelled',async()=>{
 const {host,instance}=mount(fixture());await openB(host);
 const name=host.querySelector('input')!;name.value='Unsaved B';name.dispatchEvent(new Event('input',{bubbles:true}));name.focus();await nextTick();
 const leave=instance.canLeave();await vi.waitFor(()=>expect(document.activeElement).toBe(button(host,'继续编辑')));
 button(host,'继续编辑').dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true,cancelable:true}));expect(document.activeElement).toBe(button(host,'保存后离开'));
 button(host,'保存后离开').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));expect(await leave).toBe(false);
 await nextTick();expect(document.activeElement).toBe(name);expect(host.querySelector('[role=alertdialog]')).toBeNull();
});
