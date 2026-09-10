<!-- Owns the Profile list and two-tab editor; controlled IPC persists SOUL.md and adapter drafts.
     External file fingerprints protect concurrent edits while explicit application freezes runtime state.
     The footer follows the shared settings background and save/apply layout; navigation retains discard protection.
     The model tab owns its independent preview lifetime. All product text uses shared three-language i18n. -->
<template>
 <section class="profiles-page" :class="{'is-detail':!!detail}" :aria-busy="working">
  <div class="profile-content" :inert="modalOpen?true:undefined">
   <p v-if="error" role="alert">{{ error }}</p>
   <details v-if="diagnostic"><summary>{{ tr('pDiagnostics') }}</summary><pre>{{ diagnostic }}</pre></details>
   <p v-if="notice && (!detail || tab==='role')" role="status">{{ notice }}</p>
   <p v-if="runtime&&!runtime.online" role="status">{{ tr('pOffline') }}</p>
   <p v-if="runtime?.visualStatus==='unavailable'" role="status">{{ tr('pVisualFailed') }}</p>
   <p v-if="runtime?.visualStatus==='degraded'" role="status">{{ tr('pDegraded') }}</p>
   <template v-if="!detail">
    <p v-if="loading">{{ tr('pLoading') }}</p><p v-else-if="!profiles.length">{{ tr('pEmpty') }}</p>
    <article v-for="profile in orderedProfiles" :key="profile.id" class="profile-list-row" :class="{'is-current':profile.id===runtime?.selectedId}" :aria-current="profile.id===runtime?.selectedId?'true':undefined">
     <div><h2>{{ profile.displayName }}</h2><p>{{ modelName(profile.modelName) }}<span v-if="profile.visualStatus==='invalid'"> · {{ tr('pModelInvalid') }}</span></p></div>
     <div class="profile-actions">
      <button v-if="profile.id!==runtime?.selectedId" :disabled="working" @click="activate(profile.id)">{{ tr('pSwitch') }}</button>
      <button v-else-if="!runtime?.online" :disabled="working" @click="activate(profile.id)">{{ tr('pRetry') }}</button>
      <button :disabled="working" @click="open(profile.id)">{{ tr('pEdit') }}</button>
      <button class="profile-delete" :disabled="working||profile.id===runtime?.selectedId||profiles.length<=1" :title="tr(profile.id===runtime?.selectedId?'pCurrentDelete':profiles.length<=1?'pLastDelete':'pDelete')" @click="deleteTarget=profile">{{ tr('pDelete') }}</button>
     </div>
    </article>
    <div v-if="!loading" class="profile-create-area">
     <button v-if="!creating" ref="createButton" :disabled="working" @click="showCreate">{{ tr('pCreate') }}</button>
     <form v-else class="profile-create" @submit.prevent="create">
      <label>{{ tr('pNewName') }}<input ref="createName" v-model="newName" maxlength="128" required @keydown.esc="cancelCreate" /></label>
      <button :disabled="working||!newName.trim()">{{ tr('pCreateRole') }}</button><button type="button" @click="cancelCreate">{{ tr('pCancelCreate') }}</button>
     </form>
    </div>
   </template>
   <template v-else-if="draft">
    <p v-if="draft.profile.id!==runtime?.selectedId" class="hint">{{ tr('pOther') }}</p>
    <div class="editor-tabs" role="tablist" :aria-label="tr('pTitle')">
     <button id="profile-role-tab" role="tab" :aria-selected="tab==='role'" aria-controls="profile-role-panel" @click="tab='role'">{{ tr('pRole') }}</button>
     <button id="profile-model-tab" role="tab" :aria-selected="tab==='model'" aria-controls="profile-model-panel" @click="tab='model'">{{ tr('pModelTab') }}</button>
    </div>
    <section v-if="tab==='role'" id="profile-role-panel" role="tabpanel" aria-labelledby="profile-role-tab" class="role-panel">
     <label>{{ tr('pName') }}<input v-model="draft.profile.displayName" maxlength="128" /></label>
     <label>SOUL<textarea v-model="draft.soulText" class="soul-editor" spellcheck="false" rows="16" /></label>
     <p class="hint">{{ tr('pSoulHelp') }}</p>
     <div class="profile-actions"><button @click="openSoul(false)">{{ tr('pOpenSoul') }}</button><button @click="openSoul(true)">{{ tr('pRevealSoul') }}</button></div>
     <details class="profile-info"><summary>{{ tr('pInfo') }}</summary><p>{{ tr(detail.memory.initialized?'pMemoryReady':'pMemoryNew') }}</p><code>{{ detail.memory.directory }}</code><p><code>{{ draft.profile.id }}</code></p></details>
    </section>
    <section v-else class="model-panel" id="profile-model-panel" role="tabpanel" aria-labelledby="profile-model-tab">
     <div class="model-heading"><span>{{ modelName(draft.adaptor.model?.manifest??null) }}</span><button :disabled="working" @click="importModel">{{ tr('pImport') }}</button></div>
     <ProfileModelWorkspace v-if="draft.adaptor.model" :key="draft.profile.id+draft.modelVersion" :config="draft" :resources="detail.resources" :display-language="displayLanguage" @resources="detail.resources=$event" @error="showError" />
     <p v-else>{{ tr('pImportHelp') }}</p>
    </section>
    <footer class="profile-save-bar">
     <span role="status">{{ dirty?t(displayLanguage,'configUnsaved'):pending?tr('pPending'):t(displayLanguage,'configApplied') }}</span>
     <button :class="{primary:dirty}" :disabled="working||!dirty" @click="save">{{ tr('pSave') }}</button>
     <button v-if="runtime?.selectedId===draft.profile.id" :class="{primary:!dirty&&pending}" :disabled="working||dirty||!pending" @click="activate(draft.profile.id,false,true)">{{ tr('pApply') }}</button>
     <button v-else :disabled="working||dirty" @click="activate(draft.profile.id)">{{ tr('pSwitch') }}</button>
    </footer>
   </template>
  </div>
  <div v-if="modalOpen" class="profile-dialog-backdrop">
   <section ref="dialog" role="alertdialog" aria-modal="true" :aria-label="tr(conflict?'pConflict':deleteTarget?'pDeleteTitle':busyTarget?'pBusyTitle':'pDirtyTitle')" class="profile-dialog" @keydown.esc="cancelDialog" @keydown.tab="trapFocus">
    <template v-if="conflict"><h2>{{ tr('pConflict') }}</h2><p>{{ tr('pConflictHelp') }}</p>
     <div class="conflict-texts"><label>{{ tr('pDraft') }}<textarea readonly :value="draft?.soulText" /></label><label>{{ tr('pDisk') }}<textarea readonly :value="conflict.saved.soulText" /></label></div>
     <button @click="resolveConflict(false)">{{ tr('pLoadDisk') }}</button><button @click="resolveConflict(true)">{{ tr('pKeepDraft') }}</button><button @click="conflict=undefined">{{ tr('pCancel') }}</button>
    </template>
    <template v-else-if="deleteTarget"><h2>{{ tr('pDeleteTitle') }} · {{ deleteTarget.displayName }}</h2><p>{{ tr('pDeleteHelp') }}</p><button @click="deleteTarget=undefined">{{ tr('pCancel') }}</button><button :disabled="working" @click="removeProfile">{{ tr('pDeleteConfirm') }}</button></template>
    <template v-else-if="busyTarget"><h2>{{ tr('pBusyTitle') }}</h2><p>{{ tr('pBusyHelp') }}</p><button @click="busyTarget=undefined">{{ tr('pCancel') }}</button><button @click="activate(busyTarget.id,true,busyTarget.applyOnly)">{{ tr('pBusyConfirm') }}</button></template>
    <template v-else><h2>{{ tr('pDirtyTitle') }}</h2><button @click="finishLeave(false)">{{ tr('pKeepEditing') }}</button><button @click="finishLeave(true)">{{ tr('pAbandon') }}</button><button :disabled="working" @click="saveAndLeave">{{ tr('pSaveLeave') }}</button></template>
   </section>
  </div>
 </section>
</template>
<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,onMounted,ref,watch} from 'vue';
import type {ProfileConfig,ProfileDetail,ProfileSummary} from '../../shared/contracts/profiles';
import type {ProfileRuntimeState} from '../../shared/contracts/desktop';
import {t,type DisplayLanguage} from '../../i18n';
import {profileErrorKey,type ProfileTextKey} from './profileText';
import ProfileModelWorkspace from './ProfileModelWorkspace.vue';
const props=withDefaults(defineProps<{displayLanguage?:DisplayLanguage}>(),{displayLanguage:'zh'});
const emit=defineEmits<{editing:[string|null]}>();
const tr=(key:ProfileTextKey)=>t(props.displayLanguage,key);
const profiles=ref<ProfileSummary[]>([]),runtime=ref<ProfileRuntimeState>(),detail=ref<ProfileDetail>(),draft=ref<ProfileConfig>();
const loading=ref(true),working=ref(false),error=ref(''),diagnostic=ref(''),notice=ref(''),baseline=ref(''),tab=ref<'role'|'model'>('role');
const creating=ref(false),newName=ref(''),createName=ref<HTMLInputElement>(),createButton=ref<HTMLButtonElement>();
const conflict=ref<ProfileDetail>(),deleteTarget=ref<ProfileSummary>(),busyTarget=ref<{id:string;applyOnly:boolean}>(),leaveResolve=ref<(leave:boolean)=>void>();
const dialog=ref<HTMLElement>();let returnFocus:HTMLElement|null=null,disposed=false,checkingExternal=false;
const modalOpen=computed(()=>!!(conflict.value||deleteTarget.value||busyTarget.value||leaveResolve.value));
const dirty=computed(()=>!!draft.value&&JSON.stringify(draft.value)!==baseline.value);
const pending=computed(()=>detail.value?.saved.revision!==detail.value?.applied?.revision);
const orderedProfiles=computed(()=>[...profiles.value].sort((a,b)=>Number(b.id===runtime.value?.selectedId)-Number(a.id===runtime.value?.selectedId)));
const clone=<T,>(v:T):T=>JSON.parse(JSON.stringify(v));
const modelName=(name:string|null)=>name?name.split('/').at(-1)!.replace(/\.model(?:3)?\.json$/i,''):tr('pNoModel');
watch(()=>draft.value?.profile.displayName,name=>emit('editing',name??null));
watch(modalOpen,async(open)=>{if(open){returnFocus=document.activeElement as HTMLElement;await nextTick();dialog.value?.querySelector('button')?.focus();}else{await nextTick();returnFocus?.focus();}});
function cancelDialog(){if(working.value)return;conflict.value=undefined;deleteTarget.value=undefined;busyTarget.value=undefined;finishLeave(false);}
function trapFocus(event:KeyboardEvent){const items=[...dialog.value!.querySelectorAll<HTMLElement>('button:not(:disabled),textarea')],first=items[0],last=items.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}
function showError(e:unknown){error.value=tr(profileErrorKey(e));diagnostic.value=e instanceof Error?e.message:String(e);}
async function run(action:()=>Promise<void>){if(working.value)return;working.value=true;error.value='';diagnostic.value='';try{await action();}catch(e){showError(e);}finally{working.value=false;}}
async function refresh(){const result=await window.aurabot.listProfiles();if(disposed)return;profiles.value=result.profiles;runtime.value=result.runtime;}
function loadDetail(value:ProfileDetail){detail.value=value;draft.value=clone(value.saved);baseline.value=JSON.stringify(draft.value);}
async function open(id:string){await run(async()=>{loadDetail(await window.aurabot.getProfile(id));tab.value='role';notice.value='';});}
async function showCreate(){creating.value=true;await nextTick();createName.value?.focus();}
async function cancelCreate(){creating.value=false;newName.value='';await nextTick();createButton.value?.focus();}
async function create(){await run(async()=>{loadDetail(await window.aurabot.createProfile(newName.value.trim(),props.displayLanguage));tab.value='role';newName.value='';creating.value=false;await refresh();});}
async function checkExternal(){if(working.value||checkingExternal||!draft.value||modalOpen.value||disposed)return;checkingExternal=true;const id=draft.value.profile.id;try{const current=await window.aurabot.getProfile(id);if(disposed||draft.value?.profile.id!==id)return;if(current.saved.soulFingerprint!==detail.value?.saved.soulFingerprint){if(dirty.value)conflict.value=current;else{loadDetail(current);notice.value=tr('pExternal');}}}catch(e){showError(e);}finally{checkingExternal=false;}}
function resolveConflict(keepDraft:boolean){const current=conflict.value;if(!current||!draft.value)return;if(!keepDraft)loadDetail(current);else{detail.value=current;baseline.value=JSON.stringify(current.saved);draft.value.soulFingerprint=current.saved.soulFingerprint;draft.value.revision=current.saved.revision;draft.value.adaptor.revision=current.saved.adaptor.revision;}conflict.value=undefined;}
async function save():Promise<boolean>{let saved=false;await run(async()=>{if(!draft.value)return;const id=draft.value.profile.id;try{loadDetail(await window.aurabot.saveProfile(id,{profile:clone(draft.value.profile),soulText:draft.value.soulText,expectedSoulFingerprint:draft.value.soulFingerprint,adaptor:clone(draft.value.adaptor)}));await refresh();saved=true;}catch(e){if(profileErrorKey(e)==='pConflict'){conflict.value=await window.aurabot.getProfile(id);}else throw e;}});return saved;}
async function activate(id:string,force=false,applyOnly=false){busyTarget.value=undefined;await checkExternal();if(conflict.value||dirty.value)return;await run(async()=>{const result=await window.aurabot.activateProfile(id,force,applyOnly);runtime.value=result.runtime;if(result.busy){busyTarget.value={id,applyOnly};return;}await refresh();if(detail.value?.saved.profile.id===id)loadDetail(await window.aurabot.getProfile(id));notice.value=tr(result.runtime.online?(applyOnly?'pApplied':'pSwitched'):'pOffline');});}
async function openSoul(reveal:boolean){await run(()=>window.aurabot.openProfileSoul(draft.value!.profile.id,reveal));}
async function removeProfile(){if(!deleteTarget.value)return;await run(async()=>{await window.aurabot.deleteProfile(deleteTarget.value!.id);deleteTarget.value=undefined;await refresh();});}
async function importModel(){if(!await canLeave())return;await run(async()=>{const result=await window.aurabot.importProfileModel(detail.value!.saved.profile.id);if(result){loadDetail(result);await refresh();notice.value=tr('pImported');}});}
function canLeave():Promise<boolean>{if(!dirty.value)return Promise.resolve(true);if(leaveResolve.value)return Promise.resolve(false);return new Promise(resolve=>{leaveResolve.value=resolve;});}
function finishLeave(value:boolean){leaveResolve.value?.(value);leaveResolve.value=undefined;}
async function saveAndLeave(){if(await save())finishLeave(true);}
async function leaveDetail(){if(await canLeave()){detail.value=undefined;draft.value=undefined;notice.value='';error.value='';diagnostic.value='';await refresh();}}
function beforeUnload(event:BeforeUnloadEvent){if(dirty.value){event.preventDefault();event.returnValue='';}}
let unsubscribe:(()=>void)|undefined;
onMounted(async()=>{window.addEventListener('beforeunload',beforeUnload);window.addEventListener('focus',checkExternal);try{await refresh();unsubscribe=window.aurabot.onProfilesChanged(()=>{void refresh().catch(showError);});}catch(e){showError(e);}finally{loading.value=false;}});
onBeforeUnmount(()=>{disposed=true;unsubscribe?.();window.removeEventListener('beforeunload',beforeUnload);window.removeEventListener('focus',checkExternal);finishLeave(false);emit('editing',null);});
defineExpose({canLeave,leaveDetail});
</script>
<style scoped>
.profiles-page{font-size:14px;padding-top:12px;min-height:100%}.profiles-page label{display:flex;flex-direction:column;gap:8px}.profiles-page input,.profiles-page textarea{box-sizing:border-box;max-width:100%}.profile-list-row{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:20px;border:1px solid transparent;border-bottom-color:var(--aura-border)}.profile-list-row.is-current{background:var(--aura-accent-soft);border-color:var(--aura-border);border-radius:8px;margin-bottom:4px}.profile-list-row h2{font-size:15px;margin:0 0 6px}.profile-list-row p{font-size:13px;color:var(--aura-muted);margin:0}.profile-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.profile-list-row>.profile-actions{margin-left:auto;justify-content:flex-end;flex-shrink:0}.profile-delete{color:var(--aura-text)}.profile-delete:disabled{opacity:1;background:transparent}.profile-create-area{padding:20px 0}.profile-create{display:flex;align-items:end;gap:12px}.editor-tabs{display:flex;gap:8px;border-bottom:1px solid var(--aura-border);padding-bottom:12px;margin-bottom:24px}.editor-tabs [aria-selected=true]{background:var(--aura-accent-soft);border-color:var(--aura-border)}.role-panel>label{margin-bottom:20px}.role-panel>label:first-child{max-width:440px}.soul-editor{min-height:320px;width:100%;resize:vertical;line-height:1.7;font-family:inherit}.hint{font-size:13px;color:var(--aura-muted);line-height:1.6}.profile-info{margin:24px 0}.model-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:20px}.profile-save-bar{position:sticky;bottom:0;display:flex;align-items:center;justify-content:flex-end;flex-wrap:wrap;gap:8px;padding:16px 0;background:var(--aura-bg);border-top:1px solid var(--aura-border);margin-top:24px;z-index:2}.profile-save-bar>span{flex:1;color:var(--aura-muted);font-size:12px;line-height:1.5}.profile-dialog-backdrop{position:fixed;inset:0;background:#0005;display:grid;place-items:center;z-index:100}.profile-dialog{background:var(--aura-bg,#fff);border:1px solid var(--aura-border);border-radius:12px;padding:24px;max-width:760px;width:calc(100% - 80px);max-height:85vh;overflow:auto}.profile-dialog h2{font-size:18px}.profile-dialog button{margin:8px 8px 0 0}.conflict-texts{display:grid;grid-template-columns:1fr 1fr;gap:16px}.conflict-texts textarea{min-height:220px;resize:vertical}summary{cursor:pointer;padding:8px 0}code,pre{white-space:pre-wrap;overflow-wrap:anywhere}pre{max-height:180px;overflow:auto}
@media(max-width:700px){.profile-list-row{align-items:stretch;flex-direction:column}.profile-create{align-items:stretch;flex-direction:column}.conflict-texts{grid-template-columns:1fr}.profile-save-bar>span{width:100%}}
.profiles-page.is-detail{height:100%;min-height:0;box-sizing:border-box}.is-detail>.profile-content{display:flex;flex-direction:column;height:100%;min-height:0;overflow:hidden}.is-detail .editor-tabs{flex:none;margin-bottom:16px}.role-panel{flex:1;min-height:0;overflow:auto;padding-right:8px}.model-panel{display:flex;flex-direction:column;flex:1;min-height:0}.model-panel :deep(.model-workbench){flex:1;min-height:0;height:auto}.is-detail .profile-save-bar{flex:none;position:static;margin-top:16px;padding:16px 0}.model-heading{flex:none}.is-detail>.profile-content>p{flex:none;margin:0 0 12px}
</style>
