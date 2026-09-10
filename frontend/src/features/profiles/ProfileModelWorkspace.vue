<!-- Provides an isolated model workbench: persistent preview beside a bounded resource list and editor.
     Draft entries use immutable IDs and natural-language labels. The production player validates each
     resource before enabling conversation/state use. Selecting a resource loops its preview and opens
     its editor; resident bindings are listed per state and added through a native modal picker.
     The picker reuses resource validation and draft bindings; file details stay in entry settings. Closing the tab releases rendering resources. -->
<template>
 <div class="model-workbench">
  <aside class="preview-pane">
   <Live2DPreview ref="preview" :key="config.modelVersion" :model-path="modelPath" :adaptor="config.adaptor" :profile-id="config.profile.id" :revision="config.revision" :model-version="config.modelVersion" @playback-change="playing=$event" @ready="ready=true" @error="failure" />
   <p class="preview-now" role="status">{{ playing || tr('pIdle') }}</p>
   <div class="workbench-actions"><button :disabled="!ready" @click="stop">{{ tr('pStop') }}</button></div>
   <p v-if="!ready" role="status">{{ tr('pNotReady') }}</p>
  </aside>
  <section class="resource-pane">
   <div class="resource-tabs" role="tablist" :aria-label="tr('pFile')">
    <button v-for="kind in kinds" :key="kind" role="tab" :aria-selected="filter===kind" :disabled="checking" @click="selectKind(kind)">{{ tr(kind==='motion'?'pMotion':kind==='expression'?'pExpression':'pResident') }}</button>
   </div>
   <template v-if="filter!=='resident'">
   <p v-if="!filtered.length">{{ tr(filter==='expression'?'pNoExpressions':'pNoResources') }}</p>
   <div class="resource-list" :aria-label="tr('pFile')">
    <div v-for="resource in filtered" :key="resource.file" class="resource-row" :class="{'is-selected':selectedFile===resource.file}">
     <button class="resource-choice" :aria-pressed="selectedFile===resource.file" :title="resource.file"  :disabled="!ready||checking" @click="play(resource)">{{ resourceName(resource) }}</button>
    </div>
   </div>
   <section v-if="selected" class="resource-editor">
    <p v-if="resourceError || selected.error" role="alert">{{ tr('pVerifyFailed') }}</p>
    <label v-if="labels.length>1">{{ tr('pLabels') }}<select v-model="entryId"><option v-for="[id,label] in labels" :key="id" :value="id">{{ label.display_name || id }}</option></select></label>
    <label>{{ tr('pName') }}<input :value="entry?.display_name ?? (entryId || baseName(selected.file))" maxlength="128" :placeholder="baseName(selected.file)" @input="setName(($event.target as HTMLInputElement).value)" /></label>
    <p v-if="!entry" class="hint">{{ tr('pMarkHelp') }}</p>
     <label class="check-field"><input type="checkbox" :checked="conversationEntry?.agent_enabled===true" :disabled="checking||!ready" @change="allow(($event.target as HTMLInputElement).checked)" />{{ tr('pAllow') }}</label>
     <label v-if="conversationEntry?.agent_enabled">{{ tr('pPurpose') }}<textarea v-model="conversationEntry.description" :maxlength="AVATAR_LIMITS.descriptionLength" rows="3" /></label>
     <button v-if="entry && (entry.agent_enabled || !Object.values(config.adaptor.states).some(ids=>ids.includes(entryId)))" :disabled="checking" @click="remove">{{ tr('pRemoveLabel') }}</button>
     <details class="entry-advanced"><summary>{{ tr('pAdvanced') }}</summary>
      <label v-if="entry">{{ tr('pFadeIn') }}<input type="number" min="0" max="5000" :value="entry.playback.fade_in_ms??''" @input="fade('fade_in_ms',($event.target as HTMLInputElement).value)" /></label>
      <label v-if="entry">{{ tr('pFadeOut') }}<input type="number" min="0" max="5000" :value="entry.playback.fade_out_ms??''" @input="fade('fade_out_ms',($event.target as HTMLInputElement).value)" /></label>
      <details class="resource-file"><summary>{{ tr('pFile') }}</summary><code>{{ selected.file }}</code></details>
     </details>
   </section>
   <p v-else>{{ tr('pSelectResource') }}</p>
   </template>
   <section v-else class="resident-settings">
    <p v-if="resourceError" role="alert">{{ tr('pVerifyFailed') }}</p>
    <section v-for="state in states" :key="state" class="resident-state" :data-state="state">
     <div class="resident-heading"><h3>{{ stateLabel(state) }}</h3><div class="workbench-actions"><button :disabled="!ready||checking" @click="previewState(state)">{{ tr('pStatePreview') }}</button><button :disabled="!ready||checking" @click="openPicker(state)">{{ tr('pAddResident') }}</button></div></div>
     <ul v-if="config.adaptor.states[state].length" class="resident-items">
      <li v-for="id in config.adaptor.states[state]" :key="id"><span>{{ config.adaptor.entries[id]?.display_name || baseName(config.adaptor.entries[id]?.source.file || id) }}</span><button :disabled="checking" @click="removeBinding(state,id)">{{ tr('pRemoveResident') }}</button></li>
     </ul>
     <p v-else class="hint">{{ tr('pNoResident') }}</p>
    </section>
    <details class="profile-preview-diagnostics"><summary>{{ tr('pDiagnostics') }}</summary><button @click="diagnostics=JSON.stringify(preview?.diagnostics(),null,2)">{{ tr('pReadDiagnostics') }}</button><pre>{{ diagnostics || resourceError }}</pre></details>
   </section>
  </section>
  <dialog ref="picker" class="resident-picker" :aria-label="tr('pSelectCandidates')" @cancel="cancelPicker">
   <template v-if="pickerState">
    <header class="resident-heading"><h3>{{ stateLabel(pickerState) }} · {{ tr('pSelectCandidates') }}</h3><button :disabled="checking" @click="picker?.close()">{{ tr('pDone') }}</button></header>
    <p v-if="resourceError" role="alert">{{ tr('pVerifyFailed') }}</p>
    <div class="picker-resources">
     <fieldset v-for="kind in ['motion','expression'] as const" :key="kind"><legend>{{ tr(kind==='motion'?'pMotion':'pExpression') }}</legend>
      <p v-if="!resources.some(r=>r.kind===kind)" class="hint">{{ tr(kind==='motion'?'pNoResources':'pNoExpressions') }}</p>
      <label v-for="r in resources.filter(r=>r.kind===kind)" :key="r.file" class="picker-choice" :class="{'is-selected':isBound(pickerState,r)}"><input type="checkbox" :checked="isBound(pickerState,r)" :disabled="checking||!ready||isBound(pickerState,r)||config.adaptor.states[pickerState].length>=AVATAR_LIMITS.stateEntries" @change="toggleState(pickerState,r,$event)" /><span>{{ resourceName(r) }}</span></label>
     </fieldset>
    </div>
    <p class="hint">{{ tr('pResidentLimit') }}</p>
   </template>
  </dialog>
 </div>
</template>
<script setup lang="ts">
import {computed,ref,nextTick,onBeforeUnmount} from 'vue';
import Live2DPreview from '../live2d/Live2DPreview.vue';
import {AVATAR_LIMITS,avatarResourceUrl,type AvatarResource,type AvatarState,type AvatarEntry} from '../../shared/contracts/avatar';
import type {ProfileConfig} from '../../shared/contracts/profiles';
import {t,type DisplayLanguage} from '../../i18n';
import type {ProfileTextKey} from './profileText';
const props=defineProps<{config:ProfileConfig;resources:AvatarResource[];displayLanguage:DisplayLanguage}>();
const emit=defineEmits<{resources:[AvatarResource[]];error:[unknown]}>();
const tr=(key:ProfileTextKey)=>t(props.displayLanguage,key);
const kinds=['motion','expression','resident'] as const,states:AvatarState[]=['idle','listening','thinking','speaking'];
const stateLabel=(state:AvatarState)=>tr(({idle:'pIdle',listening:'pListening',thinking:'pThinking',speaking:'pSpeaking'} as const)[state]);
const preview=ref<InstanceType<typeof Live2DPreview>>(),ready=ref(false),checking=ref(false),playing=ref(''),diagnostics=ref(''),resourceError=ref('');
const filter=ref<'motion'|'expression'|'resident'>('motion'),selectedFile=ref(''),entryId=ref('');
const picker=ref<HTMLDialogElement>(),pickerState=ref<AvatarState>();
async function openPicker(state:AvatarState){pickerState.value=state;resourceError.value='';await nextTick();picker.value?.showModal();}
function cancelPicker(event:Event){if(checking.value)event.preventDefault();}
function removeBinding(state:AvatarState,id:string){props.config.adaptor.states[state]=props.config.adaptor.states[state].filter(candidate=>candidate!==id);}
const modelPath=computed(()=>props.config.adaptor.model?avatarResourceUrl(props.config.profile.id,props.config.modelVersion,props.config.adaptor.model.manifest):'');
const filtered=computed(()=>props.resources.filter(r=>r.kind===filter.value));
const selected=computed(()=>props.resources.find(r=>r.file===selectedFile.value));
const labels=computed(()=>Object.entries(props.config.adaptor.entries).filter(([,e])=>e.source.file===selectedFile.value));
const entry=computed(()=>props.config.adaptor.entries[entryId.value]);
const conversationEntry=computed(()=>entry.value?.playback.mode!=='loop'?entry.value:labels.value.find(([,e])=>e.playback.mode!=='loop')?.[1]);
const baseName=(file:string)=>file.split('/').at(-1)!.replace(/\.(?:motion3|exp3|model3)\.json$/i,'');
const resourceName=(r:AvatarResource)=>Object.values(props.config.adaptor.entries).find(e=>e.source.file===r.file)?.display_name||baseName(r.file);
function select(r:AvatarResource){selectedFile.value=r.file;entryId.value=Object.entries(props.config.adaptor.entries).find(([,e])=>e.source.file===r.file&&e.playback.mode!=='loop')?.[0]??Object.entries(props.config.adaptor.entries).find(([,e])=>e.source.file===r.file)?.[0]??'';resourceError.value='';}
function selectKind(kind:typeof kinds[number]){filter.value=kind;if(kind==='resident')return;const first=filtered.value[0];if(first)select(first);else{selectedFile.value='';entryId.value='';}}
selectKind('motion');
let alive=true;onBeforeUnmount(()=>{alive=false;});
function failure(e:unknown){resourceError.value=e instanceof Error?e.message:String(e);}
function add(r:AvatarResource,mode:AvatarEntry['playback']['mode'],name:string){if(Object.keys(props.config.adaptor.entries).length>=AVATAR_LIMITS.entries)throw new Error('entry_limit');const id='action_'+crypto.randomUUID().replaceAll('-','');props.config.adaptor.entries[id]={kind:r.kind,display_name:name||baseName(r.file),source:{file:r.file},description:'',agent_enabled:false,playback:{mode}};return id;}
function setName(value:string){if(!selected.value)return;try{if(!entry.value){if(!value.trim())return;entryId.value=add(selected.value,selected.value.kind==='motion'?'once':'hold',value);}else entry.value.display_name=value;}catch(e){emit('error',e);}}
async function validated(r:AvatarResource){if(!ready.value||!preview.value)throw new Error('model_not_ready');const id=props.config.profile.id,version=props.config.modelVersion;await preview.value.validate(r.file,r.kind);if(!alive||id!==props.config.profile.id||version!==props.config.modelVersion)throw new Error('stale_preview');const result=await window.aurabot.verifyProfileResources(id,version,[r.file]);if(!alive||id!==props.config.profile.id||version!==props.config.modelVersion)throw new Error('stale_preview');props.config.adaptor.validated_resources=result.resources.filter(r=>r.verified).map(r=>r.file);emit('resources',result.resources);}
async function operation(action:()=>Promise<void>){if(checking.value)return;checking.value=true;resourceError.value='';try{await action();}catch(e){if(alive){failure(e);emit('error',e);}}finally{checking.value=false;}}
async function play(r:AvatarResource){if(checking.value)return;select(r);await operation(async()=>{await validated(r);await preview.value?.play({[r.kind]:r.file,loop:r.kind==='motion'});playing.value=resourceName(r);});}
function stop(){preview.value?.stop();playing.value='';}
async function allow(enabled:boolean){const r=selected.value;if(!r)return;await operation(async()=>{if(enabled)await validated(r);if(enabled&&Object.values(props.config.adaptor.entries).filter(e=>e.agent_enabled).length>=AVATAR_LIMITS.agentEntries)throw new Error('entry_limit');let target=conversationEntry.value;if(!target){if(!enabled)return;const original=entry.value;const id=add(r,r.kind==='motion'?'once':'hold',original?.display_name||baseName(r.file));target=props.config.adaptor.entries[id]!;target.description=original?.description??'';entryId.value=id;}target.agent_enabled=enabled;});}
function ensureLoop(r:AvatarResource){const existing=Object.entries(props.config.adaptor.entries).find(([,e])=>e.source.file===r.file&&e.playback.mode==='loop');if(existing)return existing[0];const original=Object.values(props.config.adaptor.entries).find(e=>e.source.file===r.file);const id=add(r,'loop',original?.display_name||baseName(r.file));if(original)props.config.adaptor.entries[id]!.description=original.description;return id;}
function isBound(state:AvatarState,r:AvatarResource){return props.config.adaptor.states[state].some(id=>props.config.adaptor.entries[id]?.source.file===r.file);}
async function toggleState(state:AvatarState,r:AvatarResource,event:Event){
 const checkbox=event.target as HTMLInputElement;const enabled=checkbox.checked;
 await operation(async()=>{
  if(!enabled){props.config.adaptor.states[state]=props.config.adaptor.states[state].filter(id=>props.config.adaptor.entries[id]?.source.file!==r.file);return;}
  if(props.config.adaptor.states[state].length>=AVATAR_LIMITS.stateEntries||Object.values(props.config.adaptor.states).flat().length>=AVATAR_LIMITS.stateBindings)throw new Error('entry_limit');
  await validated(r);
  const id=r.kind==='motion'?ensureLoop(r):Object.entries(props.config.adaptor.entries).find(([,e])=>e.source.file===r.file&&e.kind==='expression')?.[0]??add(r,'hold',baseName(r.file));
  props.config.adaptor.states[state]=[...props.config.adaptor.states[state],id];
 });
 checkbox.checked=isBound(state,r);
}
function remove(){const id=entryId.value;if(Object.values(props.config.adaptor.states).some(ids=>ids.includes(id))){props.config.adaptor.entries[id]!.agent_enabled=false;}else delete props.config.adaptor.entries[id];if(selected.value)select(selected.value);}
function fade(key:'fade_in_ms'|'fade_out_ms',value:string){if(!entry.value)return;if(value==='')delete entry.value.playback[key];else entry.value.playback[key]=Number(value);}
function previewState(state:AvatarState){preview.value?.setState(state);}
</script>
<style scoped>
.model-workbench{display:grid;grid-template-columns:minmax(240px,.9fr) minmax(300px,1.1fr);gap:24px;align-items:start;height:clamp(480px,65vh,760px)}
.preview-pane{min-width:0;position:sticky;top:0}.preview-pane :deep(.avatar-preview){height:clamp(150px,40vh,480px);min-height:150px;background:var(--aura-accent-soft);border-radius:12px}
.preview-now{min-height:20px;text-align:center;color:var(--aura-muted)}.resource-pane{height:100%;overflow:auto;min-width:0;padding-right:8px;overscroll-behavior:contain}
.resource-tabs,.workbench-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.preview-pane>.workbench-actions{justify-content:center}.resource-tabs{position:sticky;top:0;z-index:1;background:var(--aura-bg);padding-bottom:8px;margin-bottom:12px}.resource-tabs [aria-selected=true],.is-selected{background:var(--aura-accent-soft)}
.resource-list{max-height:210px;overflow:auto;border:1px solid var(--aura-border);border-radius:8px}.resource-row{display:flex;align-items:center;border-bottom:1px solid var(--aura-border)}.resource-row:last-child{border:0}.resource-row>.resource-choice{justify-content:flex-start;flex:1;min-width:0;width:100%;padding:16px 20px;border:0;border-radius:0;overflow:hidden;text-overflow:ellipsis;text-align:left;background:transparent;border-color:transparent}
.resident-state{border-bottom:1px solid var(--aura-border);padding:12px 0}.resident-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.resident-heading h3{margin:0}.resource-editor{padding-top:16px}.resource-editor label,.resident-settings label{display:flex;flex-direction:column;gap:8px;margin:12px 0}.resource-editor .check-field,.resident-settings .check-field{flex-direction:row;align-items:center}input[type=checkbox]{width:auto}.workbench-actions label{display:flex;align-items:center;gap:8px}textarea,input,select{max-width:100%;box-sizing:border-box}textarea{resize:vertical}.hint{font-size:12px;color:var(--aura-muted)}summary{cursor:pointer;padding:10px 0}code,pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}pre{max-height:180px;overflow:auto}
.resident-items{list-style:none;padding:0;margin:12px 0 0}.resident-items li{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 0;border-top:1px solid var(--aura-border)}.resident-items li span{overflow-wrap:anywhere;min-width:0}.resident-items button{flex-shrink:0}
.resident-picker{box-sizing:border-box;width:min(520px,calc(100vw - 40px));max-height:80vh;padding:24px;border:1px solid var(--aura-border);border-radius:12px;background:var(--aura-bg);color:var(--aura-text)}.resident-picker::backdrop{background:#0005}.picker-resources{max-height:50vh;overflow:auto;margin-top:16px}.picker-resources fieldset{border:0;padding:0;margin:12px 0}.picker-resources legend{font-weight:600;margin-bottom:8px}.picker-resources .picker-choice{display:flex;flex-direction:row;align-items:center;gap:12px;margin:0;padding:12px;border-radius:6px;cursor:pointer}.picker-choice span{overflow-wrap:anywhere;min-width:0}
@media(max-width:900px){.model-workbench{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr);height:100%;gap:12px}.preview-pane :deep(.avatar-preview){height:clamp(100px,22vh,180px);min-height:100px}.preview-pane{position:static;display:grid;grid-template-columns:minmax(120px,1fr) minmax(160px,1fr);grid-template-rows:auto 1fr;gap:8px 12px}.preview-pane :deep(.avatar-preview){grid-row:1 / 3;height:clamp(100px,20vh,160px)}.preview-now{margin:6px;align-self:end}.preview-pane .workbench-actions{align-content:start;justify-content:center}.preview-now{margin:6px}.resource-pane{padding:0}.resource-list{max-height:150px}}
</style>
