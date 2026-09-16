/** Mounts the actual editor/workbench and controlled IPC to verify drafts, three-language text,
 * conflict decisions, click-to-loop resource selection, modal resident bindings and stable action IDs.
 * Controlled IPC and GPU preview isolate the editor; per-dialog shims supply native methods absent
 * from jsdom, while tests exercise the actual Add/select/Done workflow and saved adaptor. */
// @vitest-environment jsdom
import {createApp,nextTick,defineComponent,h,onMounted,onUnmounted} from 'vue';
import {afterEach,expect,it,vi} from 'vitest';
import {createDefaultProfile} from '../../../src/core/profiles/soulService';
import {emptyAvatarAdaptor} from '../../../src/shared/contracts/avatar';
import type {ProfileDetail} from '../../../src/shared/contracts/profiles';
import {t,type DisplayLanguage} from '../../../src/i18n';
const playback=vi.hoisted(()=>({play:vi.fn(),active:0}));
vi.mock('../../../src/features/live2d/Live2DPreview.vue',()=>({default:defineComponent({setup(_,ctx){onMounted(()=>{playback.active++;ctx.emit('ready');});onUnmounted(()=>playback.active--);ctx.expose({play:playback.play,stop:vi.fn(),setState:vi.fn(),validate:async()=>{},diagnostics:()=>({active:playback.active})});return()=>h('div',{'data-test':'preview'});}})}));
import ProfilesPage from '../../../src/features/profiles/ProfilesPage.vue';
let dispose=()=>{};
afterEach(()=>{dispose();vi.restoreAllMocks();});
const copy=<T,>(v:T):T=>JSON.parse(JSON.stringify(v));
function fixture():ProfileDetail{const seed=createDefaultProfile();return {saved:{profile:{...seed.profile,id:'b',active:false,displayName:'B'},soulText:'# Private B\n',soulFingerprint:'original',revision:1,adaptor:emptyAvatarAdaptor(),modelVersion:''},applied:null,selected:false,memory:{directory:'/temporary/b/memory',initialized:false},resources:[]};}
function mount(detail:ProfileDetail,currentId='a',language:DisplayLanguage='zh'){
 let current=copy(detail);const runtime={selectedId:currentId,activeId:currentId,revision:1,generation:1,online:true};
 const api={listProfiles:async()=>({runtime,profiles:[{id:'a',displayName:'A',selected:true,revision:1,appliedRevision:1,modelName:'Hiyori.model3.json',visualStatus:'ready'},{id:'b',displayName:'B',selected:false,revision:1,appliedRevision:null,modelName:null,visualStatus:'unconfigured'}]}),getProfile:vi.fn(async()=>copy(current)),onProfilesChanged:()=>()=>{},activateProfile:vi.fn(async()=>({runtime})),openProfileSoul:vi.fn(async()=>{}),saveProfile:vi.fn(async(_id,input)=>{current={...current,saved:{...current.saved,profile:copy(input.profile),soulText:input.soulText,adaptor:copy(input.adaptor),soulFingerprint:'saved',revision:current.saved.revision+1}};return copy(current);}),verifyProfileResources:vi.fn(async(_id,_version,files)=>{current.resources=current.resources.map(r=>({...r,verified:files.includes(r.file)||r.verified}));return copy(current);})};
 Object.assign(window,{aurabot:api});const host=document.createElement('div');document.body.append(host);const app=createApp(ProfilesPage,{displayLanguage:language});const instance=app.mount(host) as unknown as {canLeave:()=>Promise<boolean>};dispose=()=>{app.unmount();host.remove();};return {host,api,instance};
}
function button(host:HTMLElement,text:string){const found=[...host.querySelectorAll('button')].find(b=>b.textContent?.trim()===text);if(!found)throw new Error('Missing button: '+text);return found;}
async function openB(host:HTMLElement,language:DisplayLanguage='zh'){await vi.waitFor(()=>expect(host.querySelectorAll('.profile-list-row')).toHaveLength(2));button(host.querySelectorAll('.profile-list-row')[1] as HTMLElement,t(language,'pEdit')).click();await vi.waitFor(()=>expect(host.querySelector('.soul-editor')).not.toBeNull());}
function input(node:HTMLInputElement|HTMLTextAreaElement,value:string){node.value=value;node.dispatchEvent(new Event('input',{bubbles:true}));}
async function residentChoice(host:HTMLElement,state:string,index=0,language:DisplayLanguage='zh'){
 const section=host.querySelector(`[data-state=${state}]`) as HTMLElement;
 const dialog=host.querySelector('.resident-picker') as HTMLDialogElement;
 // jsdom has no native dialog methods; keep the shim local to this mounted element.
 dialog.showModal=()=>{dialog.open=true;};dialog.close=()=>{dialog.open=false;};
 button(section,t(language,'pAddResident')).click();
 await vi.waitFor(()=>expect(dialog.open).toBe(true));
 const check=dialog.querySelectorAll<HTMLInputElement>('input[type=checkbox]')[index]!;
 expect(check).toBeDefined();await vi.waitFor(()=>expect(check.disabled).toBe(false));
 return {dialog,check};
}
async function finishResident(dialog:HTMLDialogElement,language:DisplayLanguage='zh'){
 await nextTick();
 await vi.waitFor(()=>expect(button(dialog,t(language,'pDone')).disabled).toBe(false));
 button(dialog,t(language,'pDone')).click();expect(dialog.open).toBe(false);
}
it('edits one SOUL and saves B without activation, retaining dirty navigation decisions',async()=>{
 const {host,api,instance}=mount(fixture());await openB(host);expect(host.querySelectorAll('textarea')).toHaveLength(1);
 input(host.querySelector('.soul-editor')!,'whole document\n日本語');await nextTick();const leave=instance.canLeave();await nextTick();button(host,'继续编辑').click();expect(await leave).toBe(false);
 button(host,'保存').click();await vi.waitFor(()=>expect(api.saveProfile).toHaveBeenCalledOnce());expect(api.saveProfile.mock.calls[0]![1].soulText).toBe('whole document\n日本語');expect(api.saveProfile.mock.calls[0]![1].expectedSoulFingerprint).toBe('original');expect(api.activateProfile).not.toHaveBeenCalled();
 await vi.waitFor(()=>expect(host.querySelector('[aria-busy]')?.getAttribute('aria-busy')).toBe('false'));button(host,'打开 SOUL.md').click();await vi.waitFor(()=>expect(api.openProfileSoul).toHaveBeenCalledWith('b',false));
});
it('mounts preview only in the model tab and creates a natural label in place with stable IDs',async()=>{
 const detail=fixture();detail.saved.modelVersion='v1';detail.saved.adaptor.model={manifest:'model/Test.model3.json'};detail.resources=[{file:'model/001.motion3.json',kind:'motion',referenced:true,verified:false},{file:'model/002.motion3.json',kind:'motion',referenced:true,verified:false}];
 const {host,api}=mount(detail);await openB(host);expect(playback.active).toBe(0);button(host,'模型与动作').click();await nextTick();expect(playback.active).toBe(1);
 button(host,'001').click();await vi.waitFor(()=>expect(api.verifyProfileResources).toHaveBeenCalledWith('b','v1',['model/001.motion3.json']));
 await vi.waitFor(()=>expect(playback.play).toHaveBeenCalledWith({motion:'model/001.motion3.json',loop:true}));
 expect((host.querySelector('.resource-editor input') as HTMLInputElement).value).toBe('001');expect(host.textContent).not.toContain('循环观察');expect(host.textContent).not.toContain('播放预览');expect(host.querySelector('.entry-advanced')?.lastElementChild?.className).toBe('resource-file');expect(button(host,'恢复待机')).toBeDefined();
 await vi.waitFor(()=>expect(button(host,'002').disabled).toBe(false));button(host,'002').click();await vi.waitFor(()=>expect(playback.play).toHaveBeenLastCalledWith({motion:'model/002.motion3.json',loop:true}));expect((host.querySelector('.resource-editor input') as HTMLInputElement).value).toBe('002');
 await vi.waitFor(()=>expect(button(host,'001').disabled).toBe(false));button(host,'001').click();await vi.waitFor(()=>expect(playback.play).toHaveBeenLastCalledWith({motion:'model/001.motion3.json',loop:true}));
 expect(host.querySelector('.resource-editor textarea')).toBeNull();input(host.querySelector('.resource-editor input')!,'轻轻点头');await nextTick();const allow=host.querySelector('.check-field input') as HTMLInputElement;allow.click();await vi.waitFor(()=>expect(host.querySelector('.resource-editor textarea')).not.toBeNull());await nextTick();expect((host.querySelector('.resource-editor textarea') as HTMLTextAreaElement).value).toBe('');input(host.querySelector('.resource-editor textarea')!,'保留用途');allow.click();await nextTick();expect(host.querySelector('.resource-editor textarea')).toBeNull();await vi.waitFor(()=>expect(allow.disabled).toBe(false));allow.click();await vi.waitFor(()=>expect(host.querySelector('.resource-editor textarea')).not.toBeNull());expect((host.querySelector('.resource-editor textarea') as HTMLTextAreaElement).value).toBe('保留用途');
 expect(host.textContent).not.toContain('设为默认待机');button(host,'常驻').click();await nextTick();const {dialog:idleDialog,check:idle}=await residentChoice(host,'idle');idle.click();await finishResident(idleDialog);expect(idle.checked).toBe(true);expect(idle.disabled).toBe(true);expect(host.querySelectorAll('[data-state=idle] .resident-items li')).toHaveLength(1);button(host,'动作').click();await nextTick();
 button(host,'保存').click();await vi.waitFor(()=>expect(api.saveProfile).toHaveBeenCalledOnce());const first=api.saveProfile.mock.calls[0]![1].adaptor;const id=Object.keys(first.entries).find(id=>first.entries[id].agent_enabled)!;
 expect(first.entries[id].display_name).toBe('轻轻点头');expect(first.entries[first.states.idle[0]].playback.mode).toBe('loop');expect(first.entries[id].playback.mode).toBe('once');
 await vi.waitFor(()=>expect(host.querySelector('[aria-busy]')?.getAttribute('aria-busy')).toBe('false'));input(host.querySelector('.resource-editor input')!,'Gentle nod');await nextTick();button(host,'保存').click();await vi.waitFor(()=>expect(api.saveProfile).toHaveBeenCalledTimes(2));expect(api.saveProfile.mock.calls[1]![1].adaptor.entries[id].display_name).toBe('Gentle nod');
 button(host,'常驻').click();await nextTick();expect(host.querySelectorAll('.resident-state')).toHaveLength(4);expect(host.querySelector('.resource-editor')).toBeNull();expect(host.textContent).not.toContain('动作与表情组合');expect(playback.active).toBe(1);
 const {dialog:listeningDialog,check:listening}=await residentChoice(host,'listening',1);listening.click();await nextTick();await vi.waitFor(()=>expect(api.verifyProfileResources).toHaveBeenCalledWith('b','v1',['model/002.motion3.json']));await finishResident(listeningDialog);expect(listening.checked).toBe(true);button(host,'保存').click();await vi.waitFor(()=>expect(api.saveProfile).toHaveBeenCalledTimes(3));const resident=api.saveProfile.mock.calls[2]![1].adaptor;expect(resident.entries[resident.states.listening[0]].playback.mode).toBe('loop');expect(resident.entries[resident.states.listening[0]].agent_enabled).toBe(false);
 button(host,'动作').click();await nextTick();expect((host.querySelector('.resource-editor input') as HTMLInputElement).value).toBe('Gentle nod');
 button(host,'角色').click();await nextTick();expect(playback.active).toBe(0);expect(api.activateProfile).not.toHaveBeenCalled();
});
it('shows both SOUL versions after an external conflict and rebases only after an explicit choice',async()=>{
 const {host,api}=mount(fixture());await openB(host);input(host.querySelector('.soul-editor')!,'page draft');await nextTick();const external=fixture();external.saved.soulText='disk draft';external.saved.soulFingerprint='external';external.saved.revision=2;api.getProfile.mockResolvedValue(external);
 window.dispatchEvent(new Event('focus'));await vi.waitFor(()=>expect(host.textContent).toContain('SOUL.md 已在外部修改'));const texts=host.querySelectorAll('.conflict-texts textarea');expect((texts[0] as HTMLTextAreaElement).value).toBe('page draft');expect((texts[1] as HTMLTextAreaElement).value).toBe('disk draft');button(host,'保留页面草稿').click();await nextTick();button(host,'保存').click();await vi.waitFor(()=>expect(api.saveProfile).toHaveBeenCalledOnce());expect(api.saveProfile.mock.calls[0]![1].expectedSoulFingerprint).toBe('external');expect(api.saveProfile.mock.calls[0]![1].soulText).toBe('page draft');
});
it('keeps discard decisions keyboard accessible and restores focus',async()=>{
 const {host,instance}=mount(fixture());await openB(host);const name=host.querySelector('input')!;input(name,'Unsaved B');name.focus();await nextTick();const leave=instance.canLeave();await vi.waitFor(()=>expect(document.activeElement).toBe(button(host,'继续编辑')));button(host,'继续编辑').dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true,cancelable:true}));expect(document.activeElement).toBe(button(host,'保存后离开'));button(host,'保存后离开').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));expect(await leave).toBe(false);await nextTick();expect(document.activeElement).toBe(name);
});
it.each(['zh','en','ja'] as const)('uses %s text throughout role/model editing without translating user SOUL',async language=>{
 const {host}=mount(fixture(),'a',language);await openB(host,language);expect((host.querySelector('.soul-editor') as HTMLTextAreaElement).value).toBe('# Private B\n');expect(host.textContent).toContain(t(language,'pOpenSoul'));button(host,t(language,'pModelTab')).click();await nextTick();expect(host.textContent).toContain(t(language,'pImportHelp'));if(language!=='zh')expect(host.textContent).not.toContain('未配置模型');
});
it('pins the current role with a short model name and creates after the list',async()=>{
 const {host}=mount(fixture(),'b');await vi.waitFor(()=>expect(host.querySelectorAll('.profile-list-row')).toHaveLength(2));const rows=host.querySelectorAll('.profile-list-row');expect(rows[0]!.getAttribute('aria-current')).toBe('true');expect(rows[0]!.textContent).not.toContain('当前角色');expect(rows[1]!.querySelector('p')?.textContent).toBe('Hiyori');const create=button(host,'创建');expect(rows[1]!.compareDocumentPosition(create)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();create.click();await nextTick();expect(document.activeElement).toBe(host.querySelector('.profile-create input'));button(host,'取消创建').click();await nextTick();expect(host.querySelector('.profile-create input')).toBeNull();
});
it.each(['zh','en','ja'] as const)('enables an unnamed resource with an optional description and retains resident use after label deletion (%s)',async language=>{
 const detail=fixture();detail.saved.modelVersion='v1';detail.saved.adaptor.model={manifest:'model/Test.model3.json'};detail.resources=[{file:'model/face.exp3.json',kind:'expression',referenced:true,verified:false}];
 const {host,api}=mount(detail,'a',language);await openB(host,language);button(host,t(language,'pModelTab')).click();await nextTick();button(host,t(language,'pExpression')).click();await nextTick();
 expect(host.querySelector('.resource-editor textarea')).toBeNull();const allow=host.querySelector('.resource-editor input[type=checkbox]') as HTMLInputElement;await vi.waitFor(()=>expect(allow.disabled).toBe(false));allow.click();await vi.waitFor(()=>expect(host.querySelector('.resource-editor textarea')).not.toBeNull());expect((host.querySelector('.resource-editor textarea') as HTMLTextAreaElement).value).toBe('');
 button(host,t(language,'pResident')).click();await nextTick();expect(host.textContent).not.toContain('未单独设置的状态');const {dialog,check}=await residentChoice(host,'idle',0,language);check.click();await finishResident(dialog,language);expect(check.checked).toBe(true);expect(check.disabled).toBe(true);
 button(host,t(language,'pExpression')).click();await nextTick();button(host,t(language,'pRemoveLabel')).click();await nextTick();expect(host.querySelector('.resource-editor textarea')).toBeNull();button(host,t(language,'pSave')).click();await vi.waitFor(()=>expect(api.saveProfile).toHaveBeenCalledOnce());const adaptor=api.saveProfile.mock.calls[0]![1].adaptor;expect(adaptor.states.idle).toHaveLength(1);const retained=adaptor.entries[adaptor.states.idle[0]];expect(retained.kind).toBe('expression');expect(retained.agent_enabled).toBe(false);expect(retained.description).toBe('');expect(api.activateProfile).not.toHaveBeenCalled();
});
it('retains a resident pool after a failed resource verification',async()=>{
 const detail=fixture();detail.saved.modelVersion='v1';detail.saved.adaptor.model={manifest:'model/Test.model3.json'};detail.resources=[{file:'model/bad.motion3.json',kind:'motion',referenced:true,verified:false}];const {host,api}=mount(detail);await openB(host);button(host,'模型与动作').click();await nextTick();button(host,'常驻').click();await nextTick();api.verifyProfileResources.mockRejectedValueOnce(new Error('resource_unverified'));const {dialog,check}=await residentChoice(host,'idle');check.click();await vi.waitFor(()=>expect(dialog.querySelector('[role=alert]')).not.toBeNull());await finishResident(dialog);expect(check.checked).toBe(false);expect(host.querySelectorAll('[data-state=idle] .resident-items li')).toHaveLength(0);expect(api.saveProfile).not.toHaveBeenCalled();
});
