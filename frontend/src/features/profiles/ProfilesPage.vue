<!-- Provides the actual Profile list and independent configuration editor inside Settings.
     Drafts and preview stay local; controlled IPC owns persistence/import/application. The preview
     uses the production player but never opens a dialogue, starts Memory, or changes selection. -->
<template>
  <section class="profiles-page" :aria-busy="working">
    <p v-if="error" role="alert" class="profile-error">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p class="profile-runtime">当前选中：{{ selectedName }} · {{ runtime?.online ? `运行时已激活 ${runtime.activeId}` : '运行时离线' }}</p>
    <p v-if="runtime?.error" role="status">{{ runtime.error }}</p>
    <p v-if="runtime?.visualStatus==='unavailable'" role="status">当前人偶未能加载：{{ runtime.visualError || '未配置模型或资源不可用' }}。文字和语音会话不受影响。</p>
    <p v-if="runtime?.visualStatus==='degraded'" role="status">当前视觉资源不完整，部分能力不可用；文字和语音仍可交付。请在详情页重新验证并应用。</p>
    <template v-if="!detail">
      <form class="profile-create" @submit.prevent="create">
        <label>新 Profile 名称<input v-model="newName" maxlength="128" required placeholder="例如：工作伙伴" /></label>
        <button :disabled="working || !newName.trim()">创建 Profile</button>
      </form>
      <p v-if="loading">正在加载 Profile…</p>
      <p v-else-if="!profiles.length">没有 Profile。创建后即可独立配置人格。</p>
      <article v-for="profile in profiles" :key="profile.id" class="profile-card">
        <h2>{{ profile.displayName }} <small v-if="profile.selected">已选中</small> <small v-if="runtime?.activeId===profile.id && runtime.online">已激活</small></h2>
        <p>{{ profile.modelName || '未配置模型' }} · {{ visualLabel(profile.visualStatus) }}</p>
        <p class="profile-id">{{ profile.id }}</p>
        <div class="profile-actions">
          <button :disabled="working" @click="open(profile.id)">打开详情 / 重命名</button>
          <button :disabled="working || (runtime?.activeId===profile.id && profile.revision===runtime.revision)" @click="activate(profile.id)">激活</button>
          <button :disabled="working || profile.selected || profiles.length<=1" @click="deleteTarget=profile">删除</button>
        </div>
        <p v-if="profile.selected">删除前须先切换到其他 Profile。</p>
      </article>
    </template>
    <template v-else-if="draft">
      <div class="profile-actions"><button @click="leaveDetail">← Profile 列表</button><strong>正在编辑：{{ draft.profile.displayName }}</strong><span v-if="draft.profile.id!==runtime?.selectedId">此编辑不会切换当前会话</span></div>
      <p class="profile-id">ID：{{ draft.profile.id }}</p>
      <section class="profile-card">
        <h2>基本信息与 Soul</h2>
        <label>显示名称<input v-model="draft.profile.displayName" maxlength="128" /></label>
        <template v-if="draft.profile.soul">
          <label v-for="field in soulTextFields" :key="field.key">{{ field.label }}<textarea v-model="draft.profile.soul[field.key]" :rows="field.key==='persona'?4:2" /></label>
          <label>主动程度<select v-model="draft.profile.soul.proactiveLevel"><option value="low">低</option><option value="balanced">平衡</option><option value="high">高</option></select></label>
          <label v-for="field in soulListFields" :key="field.key">{{ field.label }}（每行一项）<textarea :value="draft.profile.soul[field.key].join('\n')" @input="draft.profile.soul[field.key]=lines(($event.target as HTMLTextAreaElement).value)" /></label>
        </template>
      </section>
      <section class="profile-card">
        <h2>Memory 归属</h2>
        <code>{{ detail.memory.directory }}</code><p>{{ detail.memory.initialized?'已初始化':'尚未初始化；首次运行时按需创建' }}</p>
        <p>该目录只属于此 Profile。此页面只读取轻量状态，不启动检索服务，也不修改记忆内容。</p>
      </section>
      <section class="profile-card">
        <h2>Live2D 模型与表演适配</h2>
        <p>导入会复制完整文件夹，保留原始来源和许可文件。替换模型不改变 Soul 或 Memory。</p>
        <button :disabled="working" @click="importModel">导入模型文件夹</button>
        <p>{{ draft.adaptor.model?.manifest || '未配置模型；文字功能仍然可用' }}</p>
        <template v-if="draft.adaptor.model">
          <label><input v-model="previewOpen" type="checkbox" /> 打开独立预览</label>
          <Live2DPreview v-if="previewOpen" ref="preview" :key="draft.modelVersion" :adaptor="draft.adaptor" :model-path="modelPath" :profile-id="draft.profile.id" :revision="draft.revision" :model-version="draft.modelVersion" @validated="verified" @error="previewError" />
          <div class="profile-actions">
            <button :disabled="!previewOpen" @click="preview?.stop()">停止预览 / 恢复基线</button>
            <button :disabled="!previewOpen" @click="readDiagnostics">读取播放诊断</button>
            <label><input v-model="loopPreview" type="checkbox" /> 循环观察动作</label>
            <label>运行状态<select v-model="previewState" @change="preview?.setState(previewState)"><option v-for="state in states" :key="state" :value="state">{{ state }}</option></select></label>
          </div>
          <pre v-if="diagnostics" class="profile-diagnostics" aria-label="播放诊断">{{ diagnostics }}</pre>
          <p>预览待机多个循环，确认素材端点及幅度适合陪伴后再绑定。文件名不代表自然语言语义。</p>
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
      <footer class="profile-save-bar">
        <span role="status">{{ dirty?'有未保存修改':pending?'已保存，待应用':'已保存且已应用' }} · 保存版本 {{ detail.saved.revision }} / 已应用 {{ detail.applied?.revision ?? '无' }}</span>
        <button :disabled="working || !dirty" @click="save">保存</button>
        <button :disabled="working || !dirty" @click="loadDetail(detail)">撤销未保存编辑</button>
        <button :disabled="working || dirty || runtime?.selectedId!==draft.profile.id" @click="activate(draft.profile.id,false,true)">应用</button>
        <button v-if="runtime?.selectedId!==draft.profile.id" :disabled="working || dirty" @click="activate(draft.profile.id)">激活此 Profile</button>
      </footer>
    </template>
    <div v-if="deleteTarget || busyTarget || leaveResolve" class="profile-dialog-backdrop">
      <section role="alertdialog" aria-modal="true" class="profile-dialog">
        <template v-if="deleteTarget"><h2>删除 {{ deleteTarget.displayName }}？</h2><p>将删除该 Profile 自有的 Soul、Context、Memory 与模型文件。存在未完成任务或归档时会拒绝删除。</p><button @click="deleteTarget=undefined">取消</button><button :disabled="working" @click="removeProfile">确认删除 Profile 数据</button></template>
        <template v-else-if="busyTarget"><h2>中断当前会话后应用？</h2><p>当前回复与未交付语音会被撤销，独立 Task 的所有权保持。</p><button @click="busyTarget=undefined">取消</button><button @click="activate(busyTarget.id,true,busyTarget.applyOnly)">确认中断并应用</button></template>
        <template v-else><h2>此 Profile 有未保存修改</h2><button @click="finishLeave(false)">继续编辑</button><button @click="finishLeave(true)">放弃修改</button><button :disabled="working" @click="saveAndLeave">保存后离开</button></template>
      </section>
    </div>
  </section>
</template>
<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref} from 'vue';
import type {ProfileConfig,ProfileDetail,ProfileSummary} from '../../shared/contracts/profiles';
import type {ProfileRuntimeState} from '../../shared/contracts/desktop';
import {AVATAR_LIMITS,avatarResourceUrl,type AvatarEntry,type AvatarResource,type AvatarState} from '../../shared/contracts/avatar';
import Live2DPreview from '../live2d/Live2DPreview.vue';
const profiles=ref<ProfileSummary[]>([]),runtime=ref<ProfileRuntimeState>(),detail=ref<ProfileDetail>(),draft=ref<ProfileConfig>();
const loading=ref(true),working=ref(false),error=ref(''),notice=ref(''),newName=ref(''),baseline=ref('');
const diagnostics=ref('');
const previewOpen=ref(false),loopPreview=ref(false),previewState=ref<AvatarState>('idle'),combinedMotion=ref(''),combinedExpression=ref('');
const preview=ref<InstanceType<typeof Live2DPreview>>();
const deleteTarget=ref<ProfileSummary>(),busyTarget=ref<{id:string;applyOnly:boolean}>();
const leaveResolve=ref<((leave:boolean)=>void)>();
const states:AvatarState[]=['idle','listening','thinking','speaking'];
const soulTextFields=[{key:'name',label:'人格名称'},{key:'persona',label:'人格描述'},{key:'tone',label:'语气'},{key:'speakingStyle',label:'说话方式'},{key:'relationship',label:'关系'},{key:'reminderStyle',label:'提醒方式'},{key:'replyPreference',label:'回复偏好'}] as const;
const soulListFields=[{key:'principles',label:'原则'},{key:'taboos',label:'禁忌'},{key:'boundaries',label:'边界'},{key:'topicPreferences',label:'偏好话题'}] as const;
const dirty=computed(()=>Boolean(draft.value)&&JSON.stringify(draft.value)!==baseline.value);
const pending=computed(()=>detail.value?.saved.revision!==detail.value?.applied?.revision);
const selectedName=computed(()=>profiles.value.find(p=>p.id===runtime.value?.selectedId)?.displayName??'加载中');
const modelPath=computed(()=>draft.value?.adaptor.model?avatarResourceUrl(draft.value.profile.id,draft.value.modelVersion,draft.value.adaptor.model.manifest):'');
const loopEntries=computed(()=>Object.entries(draft.value?.adaptor.entries??{}).filter(([,e])=>e.kind==='motion'&&e.playback.mode==='loop').map(([name])=>name));
const clone=<T,>(value:T):T=>JSON.parse(JSON.stringify(value));
const lines=(value:string)=>value.split('\n').map(line=>line.trim()).filter(Boolean);
const visualLabel=(status:string)=>({unconfigured:'未配置视觉',ready:'视觉可用',pending:'待应用',invalid:'资源不可用'}[status]??status);
async function run(action:()=>Promise<void>){if(working.value)return;working.value=true;error.value='';try{await action();}catch(e){error.value=e instanceof Error?e.message:String(e);}finally{working.value=false;}}
async function refresh(){const result=await window.aurabot.listProfiles();profiles.value=result.profiles;runtime.value=result.runtime;}
function loadDetail(value:ProfileDetail){detail.value=value;draft.value=clone(value.saved);baseline.value=JSON.stringify(draft.value);}
async function open(id:string){await run(async()=>{loadDetail(await window.aurabot.getProfile(id));previewOpen.value=false;notice.value='';});}
async function create(){await run(async()=>{loadDetail(await window.aurabot.createProfile(newName.value.trim()));newName.value='';await refresh();});}
async function save():Promise<boolean>{let saved=false;await run(async()=>{if(!draft.value)return;loadDetail(await window.aurabot.saveProfile(draft.value.profile.id,{profile:clone(draft.value.profile),adaptor:clone(draft.value.adaptor)}));await refresh();saved=true;});return saved;}
async function activate(id:string,force=false,applyOnly=false){busyTarget.value=undefined;await run(async()=>{const result=await window.aurabot.activateProfile(id,force,applyOnly);runtime.value=result.runtime;if(result.busy){busyTarget.value={id,applyOnly};return;}await refresh();if(detail.value?.saved.profile.id===id)loadDetail(await window.aurabot.getProfile(id));notice.value=result.runtime.online?'Profile 已激活':'已选中；离线状态下未应用到运行时';});}
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
.profiles-page{max-width:1080px}.profile-card{margin:20px 0;padding:22px;border:1px solid #d4c8ba;border-radius:18px;background:#fffaf4a8}.profile-card h2{margin-top:0}.profile-id,code{font-size:12px;overflow-wrap:anywhere}.profile-actions,.profile-create{display:flex;flex-wrap:wrap;align-items:center;gap:12px}.profile-card label,.profile-create label{display:block;margin:12px 0}.profile-card input:not([type=checkbox]),.profile-card textarea,.profile-card select,.profile-create input{display:block;width:100%;padding:9px;border:1px solid #cfc1b3;border-radius:8px;background:#fffaf7;font:inherit;box-sizing:border-box}.profile-card textarea{resize:vertical}.profile-error{color:#a2332d}.profile-entry{border-top:1px solid #d4c8ba;margin-top:20px;padding-top:15px}.profile-resource{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:10px 0;border-bottom:1px solid #ded4c8}.profile-resource code{flex:1;min-width:180px}.profile-resource span{font-size:12px}.profile-save-bar{position:sticky;bottom:0;display:flex;gap:10px;flex-wrap:wrap;padding:16px;background:#f6f0e7f5;border-radius:12px}.profile-save-bar span{flex:1;min-width:220px}button{font:inherit;cursor:pointer;padding:9px 14px;border:1px solid #c3b7a8;border-radius:9px;background:#fff8ef;color:#40392f}button:disabled{opacity:.5;cursor:default}small{font-size:12px;background:#e5edde;padding:4px 8px;border-radius:10px}.profile-dialog-backdrop{position:fixed;inset:0;background:#201b185c;display:grid;place-items:center;z-index:110}.profile-dialog{width:min(480px,85vw);padding:28px;background:#fffaf2;border-radius:20px}.profile-dialog button{margin:8px}.profile-runtime{font-size:13px;overflow-wrap:anywhere}.profile-combination{margin-top:20px}.profile-diagnostics{max-height:260px;overflow:auto;font-size:12px;padding:12px;background:#eee8df;border-radius:10px}
</style>
