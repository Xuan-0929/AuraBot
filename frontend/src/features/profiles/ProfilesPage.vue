<!-- Provides the actual Profile list and independent configuration editor inside Settings.
     Drafts and preview stay local; controlled IPC owns persistence/import/application. The preview
     uses the production player but never opens a dialogue, starts Memory, or changes selection.
     The current character is pinned with design-system emphasis; rows show short model names and only failure statuses, share text/button alignment, switching is explicit and creation lives after the list.
     Localized draft/saved/applied labels replace revision numbers. Native disclosures keep diagnostics optional; modal focus guards preserve safe draft decisions. -->
<template>
  <section ref="profilePage" class="profiles-page" :aria-busy="working">
    <div class="profile-editor" :inert="modalOpen ? true : undefined">
    <p v-if="error" role="alert" class="profile-error">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="detail" class="profile-runtime">当前角色：{{ selectedName }}</p>
    <p v-if="runtime && !runtime.online" role="status" class="profile-runtime">对话暂不可用，恢复后使用当前角色。</p>
    <p v-if="runtime?.error" role="status">{{ runtime.error }}</p>
    <p v-if="runtime?.visualStatus==='unavailable'" role="status">当前人偶未能加载：{{ runtime.visualError || '未配置模型或资源不可用' }}。文字和语音会话不受影响。</p>
    <p v-if="runtime?.visualStatus==='degraded'" role="status">当前视觉资源不完整，部分能力不可用；文字和语音仍可交付。请在详情页重新验证并应用。</p>
    <template v-if="!detail">
      <p v-if="loading">正在加载角色…</p>
      <p v-else-if="!profiles.length">创建第一个角色，开始配置人格。</p>
      <article v-for="profile in orderedProfiles" :key="profile.id" class="profile-card profile-list-row" :class="{'is-current':profile.id===runtime?.selectedId}" :aria-current="profile.id===runtime?.selectedId ? 'true' : undefined">
        <div class="profile-list-info"><h2>{{ profile.displayName }}</h2>
        <p>{{ modelDisplayName(profile.modelName) }}<span v-if="profile.visualStatus==='invalid'"> · 模型不可用</span></p>
        </div>
        <div class="profile-actions">
          <button v-if="profile.id!==runtime?.selectedId" :disabled="working" @click="activate(profile.id)">切换到此角色</button>
          <button v-else-if="!runtime?.online" :disabled="working" @click="activate(profile.id)">重试连接</button>
          <button :disabled="working" @click="open(profile.id)">编辑</button>
          <button class="profile-delete" :title="profile.id===runtime?.selectedId?'先切换到其他角色后才能删除':profiles.length<=1?'至少保留一个角色':'删除角色'" :disabled="working || profile.id===runtime?.selectedId || profiles.length<=1" @click="deleteTarget=profile">删除</button>
        </div>

      </article>
      <div v-if="!loading" class="profile-create-area">
        <button v-if="!creating" ref="createButton" :disabled="working" @click="showCreate">创建</button>
        <form v-else class="profile-create" @submit.prevent="create">
          <label>新角色名称<input ref="createName" v-model="newName" maxlength="128" required placeholder="例如：工作伙伴" @keydown.esc="cancelCreate" /></label>
          <button :disabled="working || !newName.trim()">创建角色</button>
          <button type="button" :disabled="working" @click="cancelCreate">取消创建</button>
        </form>
      </div>
    </template>
    <template v-else-if="draft">
      <div class="profile-actions"><button @click="leaveDetail">← 角色列表</button><strong>正在编辑：{{ draft.profile.displayName }}</strong><span v-if="draft.profile.id!==runtime?.selectedId">此编辑不会切换当前会话</span></div>
      <details class="profile-metadata"><summary>角色标识</summary><code>{{ draft.profile.id }}</code></details>
      <section class="profile-card">
        <h2>人格</h2>
        <label>显示名称<input v-model="draft.profile.displayName" maxlength="128" /></label>
        <template v-if="draft.profile.soul">
          <label v-for="field in soulTextFields" :key="field.key">{{ field.label }}<textarea v-model="draft.profile.soul[field.key]" :rows="field.key==='persona'?4:2" /></label>
          <label>主动程度<select v-model="draft.profile.soul.proactiveLevel"><option value="low">低</option><option value="balanced">平衡</option><option value="high">高</option></select></label>
          <label v-for="field in soulListFields" :key="field.key">{{ field.label }}（每行一项）<textarea :value="draft.profile.soul[field.key].join('\n')" @input="draft.profile.soul[field.key]=lines(($event.target as HTMLTextAreaElement).value)" /></label>
        </template>
      </section>
      <section class="profile-card">
        <h2>Live2D与动作</h2>
        <p>需导入完整Live2D文件夹</p>
        <button :disabled="working" @click="importModel">导入模型文件夹</button>
        <p>{{ draft.adaptor.model?.manifest || '未配置模型；文字功能仍然可用' }}</p>
        <template v-if="draft.adaptor.model">
          <label><input v-model="previewOpen" type="checkbox" /> 打开独立预览</label>
          <Live2DPreview v-if="previewOpen" ref="preview" :key="draft.modelVersion" :adaptor="draft.adaptor" :model-path="modelPath" :profile-id="draft.profile.id" :revision="draft.revision" :model-version="draft.modelVersion" @validated="verified" @error="previewError" />
          <div class="profile-actions">
            <button :disabled="!previewOpen" @click="preview?.stop()">停止预览 / 恢复基线</button>

            <label><input v-model="loopPreview" type="checkbox" /> 循环观察动作</label>
            <label>运行状态<select v-model="previewState" @change="preview?.setState(previewState)"><option v-for="state in states" :key="state" :value="state">{{ state }}</option></select></label>
          </div>
          <details class="profile-preview-diagnostics"><summary>播放诊断</summary><button :disabled="!previewOpen" @click="readDiagnostics">读取播放诊断</button><pre v-if="diagnostics" class="profile-diagnostics" aria-label="播放诊断">{{ diagnostics }}</pre></details>

          <p v-if="!detail.resources.some(r=>r.kind==='expression')">无预设表情</p>
          <div class="profile-resources">
            <article v-for="resource in detail.resources" :key="resource.file" class="profile-resource">
              <code>{{ resource.file }}</code><span>{{ resource.kind }} · {{ resource.verified?'已验证':resource.error || '待播放器验证' }}</span>
              <button :disabled="working || !previewOpen" @click="playResource(resource)">预览 / 验证</button>
              <button :disabled="working || !resource.verified" @click="addEntry(resource)">添加语义标记</button>
            </article>
          </div>
          <div class="profile-combination">
            <label>组合动作<select v-model="combinedMotion" aria-label="组合动作"><option value="">无动作</option><option v-for="r in detail.resources.filter(r=>r.kind==='motion')" :key="r.file" :value="r.file">{{ r.file }}</option></select></label>
            <label>组合表情<select v-model="combinedExpression" aria-label="组合表情"><option value="">无表情</option><option v-for="r in detail.resources.filter(r=>r.kind==='expression')" :key="r.file" :value="r.file">{{ r.file }}</option></select></label>
            <button :disabled="!previewOpen" @click="playCombination">待机 → 组合动作 → 待机</button>
          </div>
        </template>
        <h3>语义能力 {{ Object.keys(draft.adaptor.entries).length }} / {{ AVATAR_LIMITS.entries }}</h3>
        <p>名称使用最多 64 位 ASCII 标识符；描述最多 256 字；最多向 Agent 开放 32 项。新标记默认不开放，预览确认后手动启用。</p>
        <article v-for="(entry,name) in draft.adaptor.entries" :key="name" class="profile-entry">
          <label>稳定名称<input :value="name" maxlength="64" pattern="[A-Za-z][A-Za-z0-9_]*" @change="renameEntry(String(name),($event.target as HTMLInputElement).value)" /></label>
          <code>{{ entry.source.file }} · {{ entry.kind }}</code>
          <label>用途说明<textarea v-model="entry.description" :maxlength="AVATAR_LIMITS.descriptionLength" /></label>
          <label>播放类型<select v-model="entry.playback.mode" @change="normalizeEntry(entry)"><option v-if="entry.kind==='motion'" value="once">一次动作</option><option v-if="entry.kind==='motion'" value="loop">运行时循环</option><option v-if="entry.kind==='expression'" value="hold">本次说话保持表情</option></select></label>
          <label><input v-model="entry.agent_enabled" type="checkbox" :disabled="entry.playback.mode==='loop'" /> 向 Agent 开放</label>
          <details><summary>高级过渡（毫秒；留空使用资源/默认值）</summary><label>进入<input :value="entry.playback.fade_in_ms ?? ''" type="number" min="0" max="5000" @input="fade(entry,'fade_in_ms',($event.target as HTMLInputElement).value)" /></label><label>退出<input :value="entry.playback.fade_out_ms ?? ''" type="number" min="0" max="5000" @input="fade(entry,'fade_out_ms',($event.target as HTMLInputElement).value)" /></label></details>
          <button @click="removeEntry(String(name))">移除标记</button>
        </article>
        <h3>运行状态基线</h3>
        <label v-for="state in states" :key="state">{{ state }}<select v-model="draft.adaptor.states[state]"><option :value="null">{{ state==='idle'?'无待机素材':'回退默认待机' }}</option><option v-for="name in loopEntries" :key="name" :value="name">{{ name }}</option></select></label>
      </section>
      <section class="profile-card">
        <h2>记忆信息</h2>
        <p>{{ detail.memory.initialized?'已初始化':'首次运行时自动初始化' }}</p>
        <details><summary>角色专属目录</summary><code>{{ detail.memory.directory }}</code></details>
      </section>
      <footer class="profile-save-bar">
        <span role="status">{{ t(displayLanguage, dirty?'configUnsaved':pending?'configSaved':'configApplied') }}</span>
        <button :class="{primary:dirty}" :disabled="working || !dirty" @click="save">保存</button>
        <button :disabled="working || !dirty" @click="loadDetail(detail)">撤销未保存编辑</button>
        <button :class="{primary:!dirty && pending}" :disabled="working || dirty || runtime?.selectedId!==draft.profile.id" @click="activate(draft.profile.id,false,true)">应用</button>
        <button v-if="runtime?.selectedId!==draft.profile.id" :disabled="working || dirty" @click="activate(draft.profile.id)">切换到此角色</button>
      </footer>
    </template>
    </div>
    <div v-if="modalOpen" class="profile-dialog-backdrop">
      <section ref="dialog" role="alertdialog" aria-modal="true" :aria-label="deleteTarget?'删除角色':busyTarget?'应用角色':'未保存修改'" class="profile-dialog" @keydown.esc="cancelDialog" @keydown.tab="trapDialogFocus">
        <template v-if="deleteTarget"><h2>删除 {{ deleteTarget.displayName }}？</h2><p>将删除此角色的人格、会话、记忆与模型文件。存在未完成任务或归档时会拒绝删除。</p><button @click="deleteTarget=undefined">取消</button><button :disabled="working" @click="removeProfile">确认删除角色数据</button></template>
        <template v-else-if="busyTarget"><h2>{{ busyTarget.applyOnly?'中断当前会话后应用修改？':'中断当前会话并切换角色？' }}</h2><p>当前回复与未交付语音会被撤销，独立 Task 的所有权保持。</p><button @click="busyTarget=undefined">取消</button><button @click="activate(busyTarget.id,true,busyTarget.applyOnly)">{{ busyTarget.applyOnly?'确认中断并应用':'确认中断并切换' }}</button></template>
        <template v-else><h2>此角色有未保存修改</h2><button @click="finishLeave(false)">继续编辑</button><button @click="finishLeave(true)">放弃修改</button><button :disabled="working" @click="saveAndLeave">保存后离开</button></template>
      </section>
    </div>
  </section>
</template>
<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,onMounted,ref,watch} from 'vue';
import type {ProfileConfig,ProfileDetail,ProfileSummary} from '../../shared/contracts/profiles';
import type {ProfileRuntimeState} from '../../shared/contracts/desktop';
import {AVATAR_LIMITS,avatarResourceUrl,type AvatarEntry,type AvatarResource,type AvatarState} from '../../shared/contracts/avatar';
import Live2DPreview from '../live2d/Live2DPreview.vue';
import { t, type DisplayLanguage } from '../../i18n';
withDefaults(defineProps<{displayLanguage?:DisplayLanguage}>(),{displayLanguage:'zh'});
const profiles=ref<ProfileSummary[]>([]),runtime=ref<ProfileRuntimeState>(),detail=ref<ProfileDetail>(),draft=ref<ProfileConfig>();
const loading=ref(true),working=ref(false),error=ref(''),notice=ref(''),newName=ref(''),baseline=ref('');
const diagnostics=ref('');
const creating=ref(false),createName=ref<HTMLInputElement>(),createButton=ref<HTMLButtonElement>();
async function showCreate(){creating.value=true;await nextTick();createName.value?.focus();}
async function cancelCreate(){creating.value=false;newName.value='';await nextTick();createButton.value?.focus();}
const profilePage=ref<HTMLElement>();
const previewOpen=ref(false),loopPreview=ref(false),previewState=ref<AvatarState>('idle'),combinedMotion=ref(''),combinedExpression=ref('');
const preview=ref<InstanceType<typeof Live2DPreview>>();
const deleteTarget=ref<ProfileSummary>(),busyTarget=ref<{id:string;applyOnly:boolean}>();
const leaveResolve=ref<((leave:boolean)=>void)>();
const dialog=ref<HTMLElement>();
const modalOpen=computed(()=>Boolean(deleteTarget.value || busyTarget.value || leaveResolve.value));
let returnFocus:HTMLElement|null=null;
watch(modalOpen,async(open)=>{if(open){returnFocus=document.activeElement as HTMLElement;await nextTick();dialog.value?.querySelector('button')?.focus();}else{await nextTick();returnFocus?.focus();}});
function cancelDialog(){if(working.value)return;deleteTarget.value=undefined;busyTarget.value=undefined;finishLeave(false);}
function trapDialogFocus(event:KeyboardEvent){const buttons=[...dialog.value!.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];const first=buttons[0],last=buttons.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}

const states:AvatarState[]=['idle','listening','thinking','speaking'];
const soulTextFields=[{key:'name',label:'人格名称'},{key:'persona',label:'人格描述'},{key:'tone',label:'语气'},{key:'speakingStyle',label:'说话方式'},{key:'relationship',label:'关系'},{key:'reminderStyle',label:'提醒方式'},{key:'replyPreference',label:'回复偏好'}] as const;
const soulListFields=[{key:'principles',label:'原则'},{key:'taboos',label:'禁忌'},{key:'boundaries',label:'边界'},{key:'topicPreferences',label:'偏好话题'}] as const;
const dirty=computed(()=>Boolean(draft.value)&&JSON.stringify(draft.value)!==baseline.value);
const pending=computed(()=>detail.value?.saved.revision!==detail.value?.applied?.revision);
const orderedProfiles=computed(()=>[...profiles.value].sort((a,b)=>Number(b.id===runtime.value?.selectedId)-Number(a.id===runtime.value?.selectedId)));
const selectedName=computed(()=>profiles.value.find(p=>p.id===runtime.value?.selectedId)?.displayName??'加载中');
const modelPath=computed(()=>draft.value?.adaptor.model?avatarResourceUrl(draft.value.profile.id,draft.value.modelVersion,draft.value.adaptor.model.manifest):'');
const loopEntries=computed(()=>Object.entries(draft.value?.adaptor.entries??{}).filter(([,e])=>e.kind==='motion'&&e.playback.mode==='loop').map(([name])=>name));
const clone=<T,>(value:T):T=>JSON.parse(JSON.stringify(value));
const lines=(value:string)=>value.split('\n').map(line=>line.trim()).filter(Boolean);
const modelDisplayName=(name:string|null)=>name?name.replace(/\.model(?:3)?\.json$/i,''):'未配置模型';
async function run(action:()=>Promise<void>){if(working.value)return;working.value=true;error.value='';try{await action();}catch(e){error.value=e instanceof Error?e.message:String(e);profilePage.value?.querySelectorAll('.profile-entry details').forEach(group=>{(group as HTMLDetailsElement).open=true;});}finally{working.value=false;}}
async function refresh(){const result=await window.aurabot.listProfiles();profiles.value=result.profiles;runtime.value=result.runtime;}
function loadDetail(value:ProfileDetail){detail.value=value;draft.value=clone(value.saved);baseline.value=JSON.stringify(draft.value);}
async function open(id:string){await run(async()=>{loadDetail(await window.aurabot.getProfile(id));previewOpen.value=false;notice.value='';});}
async function create(){await run(async()=>{loadDetail(await window.aurabot.createProfile(newName.value.trim()));newName.value='';creating.value=false;await refresh();});}
async function save():Promise<boolean>{let saved=false;await run(async()=>{if(!draft.value)return;loadDetail(await window.aurabot.saveProfile(draft.value.profile.id,{profile:clone(draft.value.profile),adaptor:clone(draft.value.adaptor)}));await refresh();saved=true;});return saved;}
async function activate(id:string,force=false,applyOnly=false){busyTarget.value=undefined;await run(async()=>{const result=await window.aurabot.activateProfile(id,force,applyOnly);runtime.value=result.runtime;if(result.busy){busyTarget.value={id,applyOnly};return;}await refresh();if(detail.value?.saved.profile.id===id)loadDetail(await window.aurabot.getProfile(id));notice.value=result.runtime.online?(applyOnly?'修改已应用':'当前角色已切换'):'当前角色已更新；对话暂不可用，恢复后使用此角色';});}
async function removeProfile(){const id=deleteTarget.value?.id;if(!id)return;await run(async()=>{await window.aurabot.deleteProfile(id);deleteTarget.value=undefined;await refresh();});}
async function importModel(){if(!await canLeave())return;await run(async()=>{const result=await window.aurabot.importProfileModel(detail.value!.saved.profile.id);if(result){previewOpen.value=false;loadDetail(result);await refresh();notice.value='模型已导入保存；请打开预览验证资源并配置语义后应用。';}});}
function addEntry(resource:AvatarResource){if(!draft.value)return;if(Object.keys(draft.value.adaptor.entries).length>=AVATAR_LIMITS.entries){error.value='语义标记已达到上限';return;}let n=1;while(draft.value.adaptor.entries['entry_'+n])n++;draft.value.adaptor.entries['entry_'+n]={kind:resource.kind,source:{file:resource.file},description:'',agent_enabled:false,playback:{mode:resource.kind==='motion'?'once':'hold'}};}
function renameEntry(oldName:string,newName:string){if(!draft.value||oldName===newName)return;if(!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(newName)||['none','neutral'].includes(newName)||draft.value.adaptor.entries[newName]){error.value='名称无效、重复或使用了保留名称';return;}draft.value.adaptor.entries[newName]=draft.value.adaptor.entries[oldName]!;delete draft.value.adaptor.entries[oldName];for(const state of states)if(draft.value.adaptor.states[state]===oldName)draft.value.adaptor.states[state]=newName;}
function removeEntry(name:string){if(!draft.value)return;delete draft.value.adaptor.entries[name];for(const state of states)if(draft.value.adaptor.states[state]===name)draft.value.adaptor.states[state]=null;}
function normalizeEntry(entry:AvatarEntry){if(entry.playback.mode==='loop')entry.agent_enabled=false;if(draft.value)for(const state of states){const name=draft.value.adaptor.states[state];if(name&&draft.value.adaptor.entries[name]?.playback.mode!=='loop')draft.value.adaptor.states[state]=null;}}
function fade(entry:AvatarEntry,key:'fade_in_ms'|'fade_out_ms',value:string){if(value==='')delete entry.playback[key];else entry.playback[key]=Number(value);}
function readDiagnostics(){try{diagnostics.value=JSON.stringify(preview.value?.diagnostics(),null,2);}catch(e){previewError(e);}}
function previewError(value:unknown){error.value=value instanceof Error?value.message:String(value);}
async function verified(value:{file:string;kind:string;profileId?:string;modelVersion?:string}){if(!detail.value||!draft.value||value.profileId!==draft.value.profile.id||value.modelVersion!==draft.value.modelVersion)return;const id=draft.value.profile.id,version=draft.value.modelVersion;try{const result=await window.aurabot.verifyProfileResources(id,version,[value.file]);if(draft.value?.profile.id===id&&draft.value.modelVersion===version){detail.value.resources=result.resources;}}catch(e){previewError(e);}}
async function playResource(resource:AvatarResource){try{await preview.value?.validate(resource.file,resource.kind);await preview.value?.play({[resource.kind]:resource.file,loop:resource.kind==='motion'&&loopPreview.value});}catch(e){previewError(e);}}
async function playCombination(){try{await preview.value?.play({motion:combinedMotion.value||undefined,expression:combinedExpression.value||undefined,loop:false});}catch(e){previewError(e);}}
function canLeave():Promise<boolean>{if(!dirty.value)return Promise.resolve(true);return new Promise(resolve=>{leaveResolve.value=resolve;});}
function finishLeave(value:boolean){leaveResolve.value?.(value);leaveResolve.value=undefined;}
async function saveAndLeave(){if(await save())finishLeave(true);}
async function leaveDetail(){if(await canLeave()){previewOpen.value=false;detail.value=undefined;draft.value=undefined;await refresh();}}
function beforeUnload(event:BeforeUnloadEvent){if(dirty.value){event.preventDefault();event.returnValue='';}}
let unsubscribe:(()=>void)|undefined;
onMounted(async()=>{window.addEventListener('beforeunload',beforeUnload);try{await refresh();unsubscribe=window.aurabot.onProfilesChanged(()=>{void refresh().catch(previewError);});}catch(e){previewError(e);}finally{loading.value=false;}});
onBeforeUnmount(()=>{unsubscribe?.();window.removeEventListener('beforeunload',beforeUnload);finishLeave(false);});
defineExpose({canLeave});
</script>
<style scoped>
.profiles-page{max-width:100%;padding-top:12px;min-height:100%;font-size:14px}
.profile-card{margin:0;padding:24px 0;border-bottom:1px solid var(--aura-border);background:transparent}
.profile-card h2{font-size:15px;font-weight:600;margin:0 0 16px}.profile-card h3{font-size:14px;margin:24px 0 16px}
.profile-card p{color:var(--aura-muted);font-size:13px;line-height:1.6;overflow-wrap:anywhere}
.profile-list-row{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:20px;border:1px solid transparent;border-bottom-color:var(--aura-border)}
.profile-list-row.is-current{background:var(--aura-accent-soft);border:1px solid var(--aura-border);border-radius:8px;margin-bottom:4px}
.profile-create-area{padding:20px 0}.profile-create-area>.profile-create{padding:0;border:0}
.profile-list-row>.profile-actions{margin-left:auto;justify-content:flex-end;flex-shrink:0}
.profile-list-info{min-width:0}.profile-list-info h2{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px}.profile-list-info p{margin:0}
.profile-id,code{font-size:12px;overflow-wrap:anywhere;color:var(--aura-muted)}
.profile-actions,.profile-create{display:flex;flex-wrap:wrap;align-items:center;gap:8px}.profile-actions{font-size:13px}.profile-actions>span{color:var(--aura-muted)}
.profile-create{border-bottom:1px solid var(--aura-border);padding:12px 0 20px;align-items:flex-end}.profile-create label{flex:1;margin:0;min-width:160px}.profile-create input{margin-top:8px}
.profile-card label{display:grid;grid-template-columns:minmax(130px,30%) minmax(0,1fr);gap:16px;align-items:start;margin:16px 0;line-height:1.6}
.profile-card label:has(input[type=checkbox]){display:flex;align-items:center;gap:8px}
.profile-card input:not([type=checkbox]),.profile-card textarea,.profile-card select,.profile-create input{width:100%;min-width:0;box-sizing:border-box;color:var(--aura-text)}
.profile-card textarea{resize:vertical}.profile-error{color:var(--aura-danger);border-left:2px solid currentColor;padding:8px 12px;overflow-wrap:anywhere}
.profile-entry{border-top:1px solid var(--aura-border);margin-top:20px;padding-top:8px}
.profile-resource{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:12px 0;border-bottom:1px solid var(--aura-border)}
.profile-resource code{flex:1;min-width:150px}.profile-resource span{font-size:12px;color:var(--aura-muted)}
.profile-save-bar{position:sticky;bottom:0;display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:16px 0;background:var(--aura-bg);border-top:1px solid var(--aura-border);z-index:2}
.profile-save-bar span{flex:1;min-width:180px;color:var(--aura-muted);font-size:12px;line-height:1.5}
.profile-actions>.profile-delete{color:var(--aura-text)}
.profile-actions>.profile-delete:disabled{opacity:1;background:transparent}
.profile-dialog-backdrop{position:fixed;inset:0;background:#0007;display:grid;place-items:center;z-index:110}
.profile-dialog{width:min(480px,85vw);padding:24px;background:var(--aura-surface);color:var(--aura-text);border:1px solid var(--aura-border);border-radius:8px}
.profile-dialog h2{font-size:18px;margin-top:0}.profile-dialog p{line-height:1.7}.profile-dialog button{margin:8px 8px 0 0}
.profile-runtime{font-size:12px;overflow-wrap:anywhere;color:var(--aura-muted);margin:4px 0 16px}.profile-combination{margin-top:20px}
.profile-diagnostics{max-height:260px;overflow:auto;font-size:12px;padding:12px;background:var(--aura-surface-soft);border-radius:6px}
details{margin:16px 0}summary{cursor:pointer;color:var(--aura-muted);font-size:13px;padding:4px 0}details[open]>summary{margin-bottom:8px}
.profile-metadata{margin:8px 0}
@media(max-width:800px){.profile-card label{grid-template-columns:1fr;gap:8px}.profile-list-row{align-items:flex-start;flex-direction:column;gap:12px}.profile-save-bar span{flex-basis:100%}.profile-resource code{flex-basis:100%}.profile-card:not(.profile-list-row){padding:20px 0}}
</style>
