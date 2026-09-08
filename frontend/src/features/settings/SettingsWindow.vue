<!-- Presents the existing settings window with one saved draft and explicit application.
     Electron owns validation/persistence and Backend restart; busy confirmation never changes a draft
     or exposes Memory. Credentials are write-only; native disclosures reveal invalid fields while shared tokens
     provide compact navigation and responsive forms. Desktop-only changes apply without restarting. -->
<template>
  <main class="settings-window">
    <div class="settings-window-shell settings-v2-layout" :inert="busy ? true : undefined">
      <aside class="settings-v2-sidebar">
        <div class="settings-v2-sidebar-brand">AuraBot</div>
        <nav class="settings-v2-navigation" aria-label="设置导航">
          <button v-for="item in sections" :key="item.key" type="button" class="settings-v2-nav-item" :aria-current="page===item.key?'page':undefined" @click="changePage(item.key)"><SettingsNavIcon :name="item.key" /><span>{{ item.label }}</span></button>
        </nav>
      </aside>
      <section class="settings-v2-content">
        <header class="settings-v2-content-header"><h1>{{ sections.find(s=>s.key===page)?.label }}</h1><span>设置</span></header>
        <div ref="pageContainer" class="settings-page-container">
          <p v-if="error" role="alert" class="integration-error">{{ error }}</p>
          <ProfilesPage v-if="page==='profiles'" ref="profilesPage" />
          <template v-else-if="draft && state">
            <form ref="settingsForm" @submit.prevent="save" @invalid.capture="revealInvalid">
              <section v-if="page==='model'" class="settings-page-section">
                <article v-for="(model,index) in [draft.backend.chat_model,draft.backend.task.model]" :key="index" class="settings-model-card">
                  <h2>{{ index===0?'对话模型':'任务模型' }}</h2>

                  <label class="settings-page-field"><span>API 地址</span><input v-model="model.base_url" placeholder="https://…/v1" autocomplete="off" /></label>
                  <label class="settings-page-field"><span>模型名称</span><input v-model="model.model_name" autocomplete="off" /></label>
                  <label class="settings-page-field"><span>API Key · {{ state.secrets[index===0?'chat_model.api_key':'task.model.api_key']?'已配置，留空保留':'尚未配置' }}</span><input v-model="model.api_key" type="password" autocomplete="new-password" /></label>
                  <label><input v-model="clearSecrets" type="checkbox" :value="index===0?'chat_model.api_key':'task.model.api_key'" /> 清除已保存密钥</label>
                  <details class="settings-advanced"><summary>高级连接设置</summary><label class="settings-page-field"><span>请求超时（秒）</span><input v-model.number="model.timeout_seconds" type="number" min="1" max="600" /></label></details>
                </article>
                <label class="settings-page-field"><span>Web 搜索密钥（可选）· {{ state.secrets['main_agent.web.tavily_api_key']?'已配置，留空保留':'尚未配置' }}</span><input v-model="draft.backend.main_agent.web.tavily_api_key" type="password" autocomplete="new-password" /></label>
                <label><input v-model="clearSecrets" type="checkbox" value="main_agent.web.tavily_api_key" /> 清除搜索密钥</label>
              </section>
              <section v-else-if="page==='voice'" class="settings-page-section">
                <label class="settings-page-toggle"><input v-model="draft.backend.main_agent.speech.enabled" type="checkbox" /> 启用语音能力</label>
                <p class="settings-hint">仅在开启桌宠麦克风后采集声音。</p>
                <label class="settings-page-field"><span>DashScope Key · {{ state.secrets['main_agent.speech.api_key']?'已配置，留空保留':'尚未配置' }}</span><input v-model="draft.backend.main_agent.speech.api_key" type="password" autocomplete="new-password" /></label>
                <label><input v-model="clearSecrets" type="checkbox" value="main_agent.speech.api_key" /> 清除语音密钥</label>
                <label class="settings-page-field"><span>音色</span><input v-model="draft.backend.main_agent.speech.tts_voice" /></label>
                <details class="settings-advanced"><summary>语音模型与资源</summary><label v-for="field in speechFields" :key="field.key" class="settings-page-field"><span>{{ field.label }}</span><input v-model="draft.backend.main_agent.speech[field.key]" /></label></details>
              </section>
              <section v-else-if="page==='desktop'" class="settings-page-section">
                <label class="settings-page-field"><span>显示语言</span><select v-model="draft.desktop.pet.displayLanguage"><option value="zh">中文</option><option value="en">English</option><option value="ja">日本語</option></select></label>
                <label class="settings-page-toggle"><input v-model="draft.desktop.pet.clickThroughPet" type="checkbox" /> 桌宠鼠标穿透</label>

                <label class="settings-page-toggle"><input v-model="draft.desktop.systemNotificationsEnabled" type="checkbox" /> 系统通知</label>
              </section>
              <section v-else-if="page==='workspace'" class="settings-page-section">
                <p class="settings-hint">任务只能访问已登记的工作目录。</p>
                <div v-for="(workspace,index) in draft.backend.app.workspaces" :key="index" class="settings-model-card">
                  <label class="settings-page-field"><span>工作区名称</span><input v-model="workspace.id" required /></label>
                  <label class="settings-page-field"><span>目录绝对路径</span><input v-model="workspace.path" required /></label>
                  <button type="button" @click="draft.backend.app.workspaces.splice(index,1)">移除登记</button>
                </div>
                <button type="button" @click="draft.backend.app.workspaces.push({id:'workspace-'+(draft.backend.app.workspaces.length+1),path:''})">添加工作区</button>
                <details class="settings-advanced"><summary>任务运行设置</summary><label class="settings-page-field"><span>执行超时（秒）</span><input v-model.number="draft.backend.task.default_timeout_seconds" type="number" min="1" max="86400" /></label>
                <label class="settings-page-field"><span>Node 可执行文件</span><input v-model="draft.backend.task.node_executable" /></label></details>
              </section>
              <section v-else class="settings-page-section">
                <h2>{{ state.online?'服务已连接':'服务未连接' }}</h2>
                <ul v-if="state.online && state.capabilities"><li>对话：{{ state.capabilities.text?'已配置':'未配置模型' }}</li><li>实时语音：{{ state.capabilities.voice?'资源已准备，开启麦克风时检查连接':'未启用或缺少密钥、SDK、模型／许可证' }}</li><li>任务：{{ state.capabilities.tasks?'已配置':'模型或 Pi runtime 不可用' }}</li></ul>
                <p>保存版本 {{ state.config.revision }} · 已应用版本 {{ state.appliedRevision ?? '尚未应用' }}</p>
                <p v-if="state.error" role="alert">{{ state.error }}</p>

              </section>
              <footer class="settings-save-bar">
                <span role="status">{{ dirty?'有未保存修改':state.pending?'已保存，待应用':'配置已应用' }}</span>
                <button type="submit" :class="{primary:dirty}" :disabled="working || !dirty">保存</button>
                <button ref="applyButton" type="button" :class="{primary:!dirty && (state.pending || !state.online)}" :disabled="working || dirty || (!state.pending && state.online)" @click="apply(false)">{{ working?'处理中…':'应用' }}</button>
              </footer>
            </form>
          </template>
        </div>
      </section>
    </div>
    <div v-if="busy" class="settings-dialog-backdrop">
      <section @keydown.esc="busy=undefined" @keydown.tab.prevent="cycleDialogFocus" role="alertdialog" aria-modal="true" aria-labelledby="restart-warning" class="settings-dialog">
        <h2 id="restart-warning">应用配置会中断正在进行的工作</h2>
        <ul><li v-if="busy.tasks">{{ busy.tasks }} 个 Task 正在运行，重启后会暂停，不会自动恢复。</li><li v-if="busy.memoryWriting">Memory 正在写入，本次写入会中断。</li><li v-if="busy.mainAgent">Aura 正在处理对话或播放回复，本轮会中断。</li></ul>
        <button ref="cancelButton" type="button" @click="busy=undefined">取消</button>
        <button type="button" :disabled="working" @click="apply(true)">仍然应用</button>
      </section>
    </div>
  </main>
</template>
<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { useAuraBotStore } from '../../app/store';
import type { BusyState, DesktopConfig, SettingsState } from '../../shared/contracts/desktop';
import ProfilesPage from '../profiles/ProfilesPage.vue';
import SettingsNavIcon, {type SettingsNavIconName} from './SettingsNavIcon.vue';
const store=useAuraBotStore();
const sections:Array<{key:SettingsNavIconName;label:string}>=[{key:'model',label:'模型'},{key:'voice',label:'实时语音'},{key:'desktop',label:'桌宠'},{key:'workspace',label:'工作目录'},{key:'profiles',label:'角色配置'},{key:'runtime',label:'运行状态'}];
const pageContainer=ref<HTMLElement>();
const profilesPage=ref<InstanceType<typeof ProfilesPage>>();
async function changePage(target:string){if(page.value==='profiles'&&target!=='profiles'&&!await profilesPage.value?.canLeave())return;page.value=target;await nextTick();if(pageContainer.value)pageContainer.value.scrollTop=0;}
const page=ref('model');const state=ref<SettingsState>();const draft=ref<DesktopConfig>();const baseline=ref('');const clearSecrets=ref<string[]>([]);
const working=ref(false);const error=ref('');const busy=ref<BusyState>();const cancelButton=ref<HTMLButtonElement>();
const dirty=computed(()=>JSON.stringify(draft.value)!==baseline.value||clearSecrets.value.length>0);
const speechFields:Array<{key:'stt_model_name'|'tts_model_name'|'tts_voice'|'silero_model_path'|'silero_license_path'|'smart_turn_model_path'|'smart_turn_license_path';label:string}>=[
 {key:'stt_model_name',label:'STT 模型'},{key:'tts_model_name',label:'TTS 模型'},
 {key:'silero_model_path',label:'VAD 模型文件'},{key:'silero_license_path',label:'VAD 许可证文件'},
 {key:'smart_turn_model_path',label:'断句模型文件'},{key:'smart_turn_license_path',label:'断句许可证文件'}
];
const settingsForm=ref<HTMLFormElement>();
const applyButton=ref<HTMLButtonElement>();
watch(busy,async(value,previous)=>{if(!value && previous){await nextTick();applyButton.value?.focus();}});
// Open synchronously so the browser can show its constraint message and focus the first invalid field.
function revealInvalid(event:Event){const group=(event.target as HTMLInputElement).closest('details');if(group)group.open=true;}
function revealAdvanced(){settingsForm.value?.querySelectorAll('details').forEach(group=>{group.open=true;});}
function cycleDialogFocus(){const next=cancelButton.value?.nextElementSibling as HTMLButtonElement|undefined;if(document.activeElement===cancelButton.value)next?.focus();else cancelButton.value?.focus();}
function load(value:SettingsState){state.value=value;draft.value=JSON.parse(JSON.stringify(value.config));baseline.value=JSON.stringify(draft.value);clearSecrets.value=[];}
async function save(){if(!draft.value)return;working.value=true;error.value='';try{load(await window.aurabot.saveSettings(JSON.parse(JSON.stringify(draft.value)),[...clearSecrets.value]));}catch(e){error.value=e instanceof Error?e.message:'保存失败';revealAdvanced();}finally{working.value=false;}}
async function apply(force:boolean){if(dirty.value)return;working.value=true;error.value='';try{const result=await window.aurabot.applySettings(force);state.value=result.state;busy.value=result.busy;if(busy.value){await nextTick();cancelButton.value?.focus();}else await store.loadInitialState();}catch(e){error.value=e instanceof Error?e.message:'应用失败';busy.value=undefined;}finally{working.value=false;}}
let unsubscribe:(()=>void)|undefined;
onMounted(async()=>{try{load(await window.aurabot.getSettings());unsubscribe=window.aurabot.onSettingsChanged(value=>{state.value=value;if(!dirty.value)load(value);});}catch(e){error.value=String(e);}});
onBeforeUnmount(()=>unsubscribe?.());
</script>
<style scoped>
.settings-window{height:100%;color:var(--aura-text);background:var(--aura-bg);font-size:14px}
.settings-v2-layout{display:grid;grid-template-columns:188px minmax(0,1fr);height:100%;overflow:hidden}
.settings-v2-sidebar{border-right:1px solid var(--aura-border);padding:28px 12px;background:var(--aura-surface-soft);overflow:auto}
.settings-v2-sidebar-brand{padding:0 12px 28px;font-size:18px;font-weight:650;letter-spacing:-.5px}
.settings-v2-navigation{display:grid;gap:4px}
.settings-v2-nav-item{display:flex;align-items:center;gap:12px;width:100%;justify-content:flex-start;padding:10px 12px;text-align:left;border-color:transparent;background:transparent;color:var(--aura-muted)}
.settings-v2-nav-item[aria-current=page]{background:var(--aura-accent-soft);color:var(--aura-accent);font-weight:600}
.settings-v2-content{display:flex;flex-direction:column;min-width:0;min-height:0;padding:0 32px}
.settings-v2-content-header{flex:none;display:flex;align-items:baseline;justify-content:space-between;padding:28px 0 20px;border-bottom:1px solid var(--aura-border)}
.settings-v2-content-header h1{font-size:22px;letter-spacing:-.4px;margin:0;font-weight:600}
.settings-v2-content-header>span{color:var(--aura-muted);font-size:12px}
.settings-page-container{min-height:0;overflow:auto;flex:1;scrollbar-gutter:stable}
.settings-page-container>form{display:flex;flex-direction:column;min-height:100%}
.settings-page-section{padding:8px 0 24px;flex:1}
.settings-model-card{padding:20px 0;border-bottom:1px solid var(--aura-border)}
.settings-model-card h2,.settings-page-section h2{font-size:15px;font-weight:600;margin:0 0 16px}
.settings-page-field{display:grid;grid-template-columns:minmax(130px,30%) minmax(0,1fr);align-items:center;gap:16px;margin:16px 0}
.settings-page-field>span{line-height:1.6;overflow-wrap:anywhere}
.settings-page-field input,.settings-page-field select{min-width:0}
.settings-page-toggle{display:flex;align-items:center;gap:8px;margin:20px 0}
.settings-hint,.settings-page-section p{color:var(--aura-muted);font-size:13px;line-height:1.6}
.settings-advanced{margin-top:20px;padding-top:12px;border-top:1px solid var(--aura-border)}
.settings-advanced summary{color:var(--aura-muted);cursor:pointer;padding:4px 0;font-size:13px}
.settings-save-bar{position:sticky;bottom:0;display:flex;align-items:center;gap:8px;margin-top:auto;padding:16px 0;background:var(--aura-bg);border-top:1px solid var(--aura-border);z-index:2}
.settings-save-bar span{flex:1;color:var(--aura-muted);font-size:12px;line-height:1.5}
.integration-error{color:var(--aura-danger);overflow-wrap:anywhere;border-left:2px solid currentColor;padding:8px 12px}
.settings-dialog-backdrop{position:fixed;inset:0;background:#0007;display:grid;place-items:center;z-index:100}
.settings-dialog{width:min(500px,90vw);padding:24px;border:1px solid var(--aura-border);border-radius:8px;background:var(--aura-surface);color:var(--aura-text)}
.settings-dialog h2{font-size:18px;margin-top:0}.settings-dialog li{margin:12px 0;line-height:1.6}.settings-dialog button{margin:12px 12px 0 0}
@media(max-width:800px){.settings-v2-layout{grid-template-columns:148px minmax(0,1fr)}.settings-v2-sidebar{padding:24px 8px}.settings-v2-sidebar-brand{padding-left:8px}.settings-v2-nav-item{gap:8px;padding:10px 8px}.settings-v2-content{padding:0 20px}.settings-v2-content-header{padding:24px 0 16px}.settings-page-field{grid-template-columns:1fr;gap:8px;margin:16px 0}.settings-save-bar{flex-wrap:wrap}.settings-save-bar span{min-width:150px}}
@media(max-width:500px){.settings-v2-layout{display:flex;flex-direction:column}.settings-v2-sidebar{padding:12px;border-right:0;border-bottom:1px solid var(--aura-border);overflow:visible}.settings-v2-sidebar-brand{display:none}.settings-v2-navigation{display:flex;overflow:auto}.settings-v2-nav-item{width:auto;white-space:nowrap}.settings-v2-nav-item :deep(svg){display:none}.settings-v2-content{flex:1}}
</style>
