<!-- Presents the existing settings window with one saved draft and explicit application.
     Electron owns validation/persistence and Backend restart; busy confirmation never changes a draft
     or exposes Memory. Credentials are write-only, and desktop-only changes apply without restarting. -->
<template>
  <main class="settings-window">
    <div class="settings-window-shell settings-v2-layout" :inert="busy ? true : undefined">
      <aside class="settings-v2-sidebar">
        <div class="settings-v2-sidebar-brand">AuraBot</div>
        <nav class="settings-v2-navigation" aria-label="设置导航">
          <button v-for="item in sections" :key="item.key" type="button" class="settings-v2-nav-item" :aria-current="page===item.key?'page':undefined" @click="changePage(item.key)">{{ item.label }}</button>
        </nav>
      </aside>
      <section class="settings-v2-content">
        <header class="settings-v2-content-header"><div><span>设置</span><h1>{{ sections.find(s=>s.key===page)?.label }}</h1><p>保存保留修改；应用后才改变当前运行配置。</p></div></header>
        <div class="settings-page-container">
          <p v-if="error" role="alert" class="integration-error">{{ error }}</p>
          <ProfilesPage v-if="page==='profiles'" ref="profilesPage" />
          <template v-else-if="draft && state">
            <form @submit.prevent="save">
              <section v-if="page==='model'" class="settings-page-section">
                <article v-for="(model,index) in [draft.backend.chat_model,draft.backend.task.model]" :key="index" class="settings-model-card">
                  <h2>{{ index===0?'Main Agent':'Task / Pi' }}</h2>
                  <p>OpenAI-compatible API</p>
                  <label class="settings-page-field"><span>API 地址</span><input v-model="model.base_url" placeholder="https://…/v1" autocomplete="off" /></label>
                  <label class="settings-page-field"><span>模型名称</span><input v-model="model.model_name" autocomplete="off" /></label>
                  <label class="settings-page-field"><span>API Key · {{ state.secrets[index===0?'chat_model.api_key':'task.model.api_key']?'已配置，留空保留':'尚未配置' }}</span><input v-model="model.api_key" type="password" autocomplete="new-password" /></label>
                  <label><input v-model="clearSecrets" type="checkbox" :value="index===0?'chat_model.api_key':'task.model.api_key'" /> 清除已保存密钥</label>
                  <label class="settings-page-field"><span>请求超时（秒）</span><input v-model.number="model.timeout_seconds" type="number" min="1" max="600" /></label>
                </article>
                <label class="settings-page-field"><span>Web 搜索密钥（可选）· {{ state.secrets['main_agent.web.tavily_api_key']?'已配置':'尚未配置' }}</span><input v-model="draft.backend.main_agent.web.tavily_api_key" type="password" autocomplete="new-password" /></label>
                <label><input v-model="clearSecrets" type="checkbox" value="main_agent.web.tavily_api_key" /> 清除搜索密钥</label>
              </section>
              <section v-else-if="page==='voice'" class="settings-page-section">
                <label class="settings-page-toggle"><input v-model="draft.backend.main_agent.speech.enabled" type="checkbox" /> 启用语音能力</label>
                <p>开启桌宠麦克风后才采集声音。语音回复整句播放完成后进入聊天历史。</p>
                <label class="settings-page-field"><span>DashScope Key · {{ state.secrets['main_agent.speech.api_key']?'已配置，留空保留':'尚未配置' }}</span><input v-model="draft.backend.main_agent.speech.api_key" type="password" autocomplete="new-password" /></label>
                <label><input v-model="clearSecrets" type="checkbox" value="main_agent.speech.api_key" /> 清除语音密钥</label>
                <label v-for="field in speechFields" :key="field.key" class="settings-page-field"><span>{{ field.label }}</span><input v-model="draft.backend.main_agent.speech[field.key]" /></label>
              </section>
              <section v-else-if="page==='desktop'" class="settings-page-section">
                <label class="settings-page-field"><span>显示语言</span><select v-model="draft.desktop.pet.displayLanguage"><option value="zh">中文</option><option value="en">English</option><option value="ja">日本語</option></select></label>
                <label class="settings-page-toggle"><input v-model="draft.desktop.pet.clickThroughPet" type="checkbox" /> 桌宠鼠标穿透</label>
                <p>人格与 Live2D 模型在 Profile 管理中独立配置。</p>
                <label class="settings-page-toggle"><input v-model="draft.desktop.systemNotificationsEnabled" type="checkbox" /> 系统通知</label>
              </section>
              <section v-else-if="page==='workspace'" class="settings-page-section">
                <p>Main Agent 只能向登记的工作目录分派任务。</p>
                <div v-for="(workspace,index) in draft.backend.app.workspaces" :key="index" class="settings-model-card">
                  <label class="settings-page-field"><span>工作区名称</span><input v-model="workspace.id" required /></label>
                  <label class="settings-page-field"><span>目录绝对路径</span><input v-model="workspace.path" required /></label>
                  <button type="button" @click="draft.backend.app.workspaces.splice(index,1)">移除登记</button>
                </div>
                <button type="button" @click="draft.backend.app.workspaces.push({id:'workspace-'+(draft.backend.app.workspaces.length+1),path:''})">添加工作区</button>
                <label class="settings-page-field"><span>Task 默认执行超时（秒）</span><input v-model.number="draft.backend.task.default_timeout_seconds" type="number" min="1" max="86400" /></label>
                <label class="settings-page-field"><span>Node 可执行文件</span><input v-model="draft.backend.task.node_executable" /></label>
              </section>
              <section v-else class="settings-page-section">
                <h2>{{ state.online?'Backend 已连接':'Backend 未连接' }}</h2>
                <ul v-if="state.online && state.capabilities"><li>Main Agent 文字：{{ state.capabilities.text?'已配置':'未配置模型' }}</li><li>实时语音：{{ state.capabilities.voice?'资源已准备，开启麦克风时检查连接':'未启用或缺少密钥、SDK、模型／许可证' }}</li><li>Task / Pi：{{ state.capabilities.tasks?'已配置':'模型或 Pi runtime 不可用' }}</li></ul>
                <p>保存版本 {{ state.config.revision }} · 已应用版本 {{ state.appliedRevision ?? '尚未应用' }}</p>
                <p v-if="state.error" role="alert">{{ state.error }}</p>
                <p>后台记忆由 Aura 自动维护；这里不提供记忆内容或操作入口。</p>
              </section>
              <footer class="settings-save-bar">
                <span role="status">{{ dirty?'有未保存修改':state.pending?'已保存，待应用':'配置已应用' }}</span>
                <button type="submit" :disabled="working || !dirty">保存</button>
                <button type="button" :disabled="working || dirty || (!state.pending && state.online)" @click="apply(false)">{{ working?'处理中…':'应用' }}</button>
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
import { computed, nextTick, onMounted, onBeforeUnmount, ref } from 'vue';
import { useAuraBotStore } from '../../app/store';
import type { BusyState, DesktopConfig, SettingsState } from '../../shared/contracts/desktop';
import ProfilesPage from '../profiles/ProfilesPage.vue';
const store=useAuraBotStore();
const sections=[{key:'model',label:'模型'},{key:'voice',label:'实时语音'},{key:'desktop',label:'桌宠'},{key:'workspace',label:'工作目录'},{key:'profiles',label:'Profile 管理'},{key:'runtime',label:'运行状态'}];
const profilesPage=ref<InstanceType<typeof ProfilesPage>>();
async function changePage(target:string){if(page.value==='profiles'&&target!=='profiles'&&!await profilesPage.value?.canLeave())return;page.value=target;}
const page=ref('model');const state=ref<SettingsState>();const draft=ref<DesktopConfig>();const baseline=ref('');const clearSecrets=ref<string[]>([]);
const working=ref(false);const error=ref('');const busy=ref<BusyState>();const cancelButton=ref<HTMLButtonElement>();
const dirty=computed(()=>JSON.stringify(draft.value)!==baseline.value||clearSecrets.value.length>0);
const speechFields:Array<{key:'stt_model_name'|'tts_model_name'|'tts_voice'|'silero_model_path'|'silero_license_path'|'smart_turn_model_path'|'smart_turn_license_path';label:string}>=[
 {key:'stt_model_name',label:'STT 模型'},{key:'tts_model_name',label:'TTS 模型'},{key:'tts_voice',label:'音色'},
 {key:'silero_model_path',label:'VAD 模型文件'},{key:'silero_license_path',label:'VAD 许可证文件'},
 {key:'smart_turn_model_path',label:'断句模型文件'},{key:'smart_turn_license_path',label:'断句许可证文件'}
];
function cycleDialogFocus(){const next=cancelButton.value?.nextElementSibling as HTMLButtonElement|undefined;if(document.activeElement===cancelButton.value)next?.focus();else cancelButton.value?.focus();}
function load(value:SettingsState){state.value=value;draft.value=JSON.parse(JSON.stringify(value.config));baseline.value=JSON.stringify(draft.value);clearSecrets.value=[];}
async function save(){if(!draft.value)return;working.value=true;error.value='';try{load(await window.aurabot.saveSettings(JSON.parse(JSON.stringify(draft.value)),[...clearSecrets.value]));}catch(e){error.value=e instanceof Error?e.message:'保存失败';}finally{working.value=false;}}
async function apply(force:boolean){if(dirty.value)return;working.value=true;error.value='';try{const result=await window.aurabot.applySettings(force);state.value=result.state;busy.value=result.busy;if(busy.value){await nextTick();cancelButton.value?.focus();}else await store.loadInitialState();}catch(e){error.value=e instanceof Error?e.message:'应用失败';busy.value=undefined;}finally{working.value=false;}}
let unsubscribe:(()=>void)|undefined;
onMounted(async()=>{try{load(await window.aurabot.getSettings());unsubscribe=window.aurabot.onSettingsChanged(value=>{state.value=value;if(!dirty.value)load(value);});}catch(e){error.value=String(e);}});
onBeforeUnmount(()=>unsubscribe?.());
</script>
<style scoped>
.settings-page-field input,.settings-page-field select{width:100%;padding:10px 12px;border:1px solid #bdb5ac;border-radius:10px;background:#ffffff9c;font:inherit;color:inherit}
.settings-model-card{margin-bottom:20px;padding:20px}.settings-page-field{margin:16px 0}.settings-save-bar{position:sticky;bottom:0;display:flex;align-items:center;gap:14px;margin-top:24px;padding:16px;background:#f6f1e9f5;border-radius:14px}.settings-save-bar span{flex:1}button{font:inherit;cursor:pointer;padding:9px 16px;border:1px solid #c3b7a8;border-radius:10px;background:#fff8ef;color:#40392f}button:disabled{opacity:.5;cursor:default}.integration-error{color:#973d32}.settings-dialog-backdrop{position:fixed;inset:0;background:#201b185c;display:grid;place-items:center;z-index:100}.settings-dialog{width:min(500px,90vw);padding:28px;border-radius:22px;background:#fffaf2;color:#40392f}.settings-dialog li{margin:12px 0}.settings-dialog button{margin:12px 12px 0 0}@media(max-width:800px){.settings-v2-layout{display:block}.settings-v2-sidebar{width:auto}.settings-v2-navigation{display:flex;flex-wrap:wrap}.settings-v2-content{padding:18px}}
</style>
