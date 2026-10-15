<!-- Owns global Task/search, desktop and workspace settings with explicit save/apply.
     Profile editing owns conversation models and voice; navigation preserves its dirty drafts.
     Main validates settings and masked secrets, while busy restart confirmation fences live work. -->
<template>
  <main class="settings-window">
    <div class="settings-window-shell settings-v2-layout" :inert="busy ? true : undefined">
      <aside class="settings-v2-sidebar">
        <div class="settings-v2-sidebar-brand">{{ t(displayLanguage, 'settings') }}</div>
        <nav class="settings-v2-navigation" :aria-label="t(displayLanguage,'settingsNavigation')">
          <button v-for="item in sections" :key="item.key" type="button" class="settings-v2-nav-item" :aria-current="page===item.key?'page':undefined" @click="changePage(item.key)"><SettingsNavIcon :name="item.key" /><span>{{ item.label }}</span></button>
        </nav>
      </aside>
      <section class="settings-v2-content">
        <header class="settings-v2-content-header"><button v-if="page==='profiles' && editingName!==null" class="profile-back" :aria-label="t(displayLanguage,'pBack')" @click="profilesPage?.leaveDetail()"><Icon :icon="backIcon" /></button><h1>{{ sections.find(s=>s.key===page)?.label }}<template v-if="page==='profiles' && editingName!==null"> · {{ editingName }}</template></h1></header>
        <div ref="pageContainer" class="settings-page-container">
          <p v-if="error" role="alert" class="integration-error">{{ error }}</p>
          <ProfilesPage v-if="page==='profiles'" ref="profilesPage" :display-language="displayLanguage" @editing="editingName=$event" />
          <template v-else-if="draft && state">
            <form ref="settingsForm" @submit.prevent="save" @invalid.capture="revealInvalid">
              <section v-if="page==='model'" class="settings-page-section">
                <article class="settings-model-card">
                  <h2>{{ t(displayLanguage, 'taskModel') }}</h2>

                  <label class="settings-page-field"><span>{{ t(displayLanguage, 'apiAddress') }}</span><input v-model="draft.backend.task.model.base_url" placeholder="https://…/v1" autocomplete="off" /></label>
                  <label class="settings-page-field"><span>{{ t(displayLanguage, 'modelName') }}</span><input v-model="draft.backend.task.model.model_name" autocomplete="off" /></label>
                  <label class="settings-page-field"><span>{{ t(displayLanguage, 'apiKey') }}</span><SecretInput v-model="draft.backend.task.model.api_key" :display-language="displayLanguage" :preview="state.secretPreviews?.['task.model.api_key']" :cleared="clearSecrets.includes('task.model.api_key')" @update:model-value="updateSecretIntent('task.model.api_key', $event)" /></label>
                  <details class="settings-advanced"><summary>{{ t(displayLanguage, 'advancedConnectionSettings') }}</summary><label class="settings-page-field"><span>{{ t(displayLanguage, 'requestTimeoutSeconds') }}</span><input v-model.number="draft.backend.task.model.timeout_seconds" type="number" min="1" max="600" /></label></details>
                </article>
                <article class="settings-model-card">
                  <h2>{{ t(displayLanguage, 'searchInterface') }}</h2>
                  <label class="settings-page-field">
                    <span>{{ t(displayLanguage, 'searchProvider') }}</span>
                    <select v-model="draft.backend.main_agent.web.provider" name="search-provider">
                      <option value="tavily">Tavily</option>
                      <option value="infoquest" disabled>InfoQuest（{{ t(displayLanguage, 'providerUnavailable') }}）</option>
                    </select>
                  </label>
                  <label class="settings-page-field"><span>{{ t(displayLanguage, 'apiKey') }}</span><SecretInput v-model="draft.backend.main_agent.web.api_key" :display-language="displayLanguage" :preview="state.secretPreviews?.['main_agent.web.api_key']" :cleared="clearSecrets.includes('main_agent.web.api_key')" @update:model-value="updateSecretIntent('main_agent.web.api_key', $event)" /></label>
                  <p class="settings-hint">{{ t(displayLanguage, 'searchKeyHint') }}</p>
                </article>
              </section>
              <section v-else-if="page==='general'" class="settings-page-section">
                <article class="settings-model-card"><h2>{{ t(displayLanguage, 'desktopPet') }}</h2>
                <label class="settings-page-field"><span>{{ t(displayLanguage, 'displayLanguage') }}</span><select v-model="draft.desktop.pet.displayLanguage"><option value="zh">中文</option><option value="en">English</option><option value="ja">日本語</option></select></label>
                <label class="settings-page-toggle"><input v-model="draft.desktop.pet.clickThroughPet" type="checkbox" /> {{ t(displayLanguage, 'clickThroughPet') }}</label>

                <section v-if="state.launchOptions?.debug" data-debug-settings>
                  <h3>{{ debugText(displayLanguage, 'launchMode') }}</h3>
                  <p class="settings-hint">{{ debugText(displayLanguage, 'hint') }}</p>
                  <div class="debug-actions">
                    <button type="button" @click="debugAction('open')">{{ debugText(displayLanguage, 'open') }}</button>
                    <button type="button" @click="debugAction('clear')">{{ debugText(displayLanguage, 'clear') }}</button>
                    <button type="button" @click="debugAction('folder')">{{ debugText(displayLanguage, 'folder') }}</button>
                  </div>
                </section>
                </article>
                <article v-if="runtimeHints.length || state.error" class="settings-model-card">
                  <h2>{{ t(displayLanguage, 'runtimeHints') }}</h2>
                  <ul v-if="runtimeHints.length"><li v-for="hint in runtimeHints" :key="hint">{{ hint }}</li></ul>
                  <p v-if="state.error" role="alert">{{ state.error }}</p>
                </article>
              </section>
              <section v-else-if="page==='workspace'" class="settings-page-section">
                <p class="settings-hint">{{ t(displayLanguage, 'workspaceHint') }}</p>
                <label class="settings-page-field"><span>{{ t(displayLanguage, 'dataDirectory') }}</span><input :value="draft.backend.app.data_dir" readonly /></label>
                <p class="settings-hint">{{ t(displayLanguage, 'dataDirectoryHint') }}</p>
                <div v-for="(workspace,index) in draft.backend.app.workspaces" :key="index" class="settings-model-card">
                  <label class="settings-page-field"><span>{{ t(displayLanguage, 'workspaceName') }}</span><input v-model="workspace.id" required /></label>
                  <label class="settings-page-field"><span>{{ t(displayLanguage, 'workspacePath') }}</span><input v-model="workspace.path" required /></label>
                  <button type="button" @click="draft.backend.app.workspaces.splice(index,1)">{{ t(displayLanguage, 'removeWorkspace') }}</button>
                </div>
                <button type="button" class="settings-workspace-add" @click="draft.backend.app.workspaces.push({id:'workspace-'+(draft.backend.app.workspaces.length+1),path:''})">{{ t(displayLanguage, 'addWorkspace') }}</button>
                <details class="settings-advanced"><summary>{{ t(displayLanguage, 'taskRuntimeSettings') }}</summary><label class="settings-page-field"><span>{{ t(displayLanguage, 'executionTimeoutSeconds') }}</span><input v-model.number="draft.backend.task.default_timeout_seconds" type="number" min="1" max="86400" /></label>
                <label class="settings-page-field"><span>{{ t(displayLanguage, 'nodeExecutable') }}</span><input v-model="draft.backend.task.node_executable" /></label></details>
              </section>
              <footer class="settings-save-bar">
                <span role="status">{{ configStatus }}</span>
                <button type="submit" :class="{primary:dirty}" :disabled="working || !dirty">{{ t(displayLanguage, 'save') }}</button>
                <button ref="applyButton" type="button" :class="{primary:!dirty && (state.pending || !state.online || !store.profileRuntime?.online)}" :disabled="working || dirty || (!state.pending && state.online && store.profileRuntime?.online)" @click="apply(false)">{{ t(displayLanguage, working?'processing':'apply') }}</button>
              </footer>
            </form>
          </template>
        </div>
      </section>
    </div>
    <div v-if="busy" class="settings-dialog-backdrop">
      <section @keydown.esc="busy=undefined" @keydown.tab.prevent="cycleDialogFocus" role="alertdialog" aria-modal="true" aria-labelledby="restart-warning" class="settings-dialog">
        <h2 id="restart-warning">{{ t(displayLanguage, 'restartWarningTitle') }}</h2>
        <ul><li v-if="busy.tasks">{{ interpolate(t(displayLanguage, 'taskRestartWarning'), {count: busy.tasks}) }}</li><li v-if="busy.memoryWriting">{{ t(displayLanguage, 'memoryRestartWarning') }}</li><li v-if="busy.mainAgent">{{ t(displayLanguage, 'agentRestartWarning') }}</li></ul>
        <button ref="cancelButton" type="button" @click="busy=undefined">{{ t(displayLanguage, 'cancel') }}</button>
        <button type="button" :disabled="working" @click="apply(true)">{{ t(displayLanguage, 'applyAnyway') }}</button>
      </section>
    </div>
  </main>
</template>
<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { useAuraBotStore } from '../../app/store';
import type { BusyState, DesktopConfig, SettingsState } from '../../shared/contracts/desktop';
import {Icon} from '@iconify/vue';
import solarIcons from '@iconify-json/solar/icons.json';
const backIcon={...solarIcons.icons['arrow-left-linear'],width:24,height:24};
const editingName=ref<string|null>(null);
import ProfilesPage from '../profiles/ProfilesPage.vue';
import SecretInput from './SecretInput.vue';
import { interpolate, t } from '../../i18n';
import { debugText } from '../live-debug/text';
import SettingsNavIcon, {type SettingsNavIconName} from './SettingsNavIcon.vue';
const store=useAuraBotStore();
const sections=computed<Array<{key:SettingsNavIconName;label:string}>>(()=>[{key:'model',label:t(displayLanguage.value,'capabilityIntegrations')},{key:'general',label:t(displayLanguage.value,'petStatus')},{key:'workspace',label:t(displayLanguage.value,'pWorkspaces')},{key:'profiles',label:t(displayLanguage.value,'pTitle')}]);
const pageContainer=ref<HTMLElement>();
const profilesPage=ref<InstanceType<typeof ProfilesPage>>();
async function changePage(target:string){if(page.value==='profiles'&&target!=='profiles'&&!await profilesPage.value?.canLeave())return;page.value=target;await nextTick();if(pageContainer.value)pageContainer.value.scrollTop=0;}
const page=ref('model');const state=ref<SettingsState>();const draft=ref<DesktopConfig>();const baseline=ref('');const clearSecrets=ref<string[]>([]);
const working=ref(false);const error=ref('');const busy=ref<BusyState>();const cancelButton=ref<HTMLButtonElement>();
const dirty=computed(()=>JSON.stringify(draft.value)!==baseline.value||clearSecrets.value.length>0);
const displayLanguage=computed(()=>draft.value?.desktop.pet.displayLanguage ?? store.displayLanguage);
const configStatus=computed(()=>t(displayLanguage.value,dirty.value?'configUnsaved':state.value?.pending?'configSaved':'configApplied'));
const runtimeHints=computed(()=>{
  const current=state.value;
  if(!current)return [];
  if(!current.online)return [t(displayLanguage.value,'serviceDisconnected')];
  const capabilities=current.capabilities;
  if(!capabilities)return [];
  const hints=[];
  if(!capabilities.text)hints.push(t(displayLanguage.value,'chatNotConfigured'));

  if(!capabilities.tasks)hints.push(t(displayLanguage.value,'tasksNotConfigured'));
  return hints;
});
const settingsForm=ref<HTMLFormElement>();
const applyButton=ref<HTMLButtonElement>();
watch(busy,async(value,previous)=>{if(!value && previous){await nextTick();applyButton.value?.focus();}});
// Open synchronously so the browser can show its constraint message and focus the first invalid field.
function revealInvalid(event:Event){const group=(event.target as HTMLInputElement).closest('details');if(group)group.open=true;}
function revealAdvanced(){settingsForm.value?.querySelectorAll('details').forEach(group=>{group.open=true;});}
function cycleDialogFocus(){const next=cancelButton.value?.nextElementSibling as HTMLButtonElement|undefined;if(document.activeElement===cancelButton.value)next?.focus();else cancelButton.value?.focus();}
function updateSecretIntent(path:string,value:string){clearSecrets.value=clearSecrets.value.filter(item=>item!==path);if(!value && state.value?.secrets[path])clearSecrets.value.push(path);}
function load(value:SettingsState){state.value=value;draft.value=JSON.parse(JSON.stringify(value.config));baseline.value=JSON.stringify(draft.value);clearSecrets.value=[];}
async function save(){if(!draft.value)return;working.value=true;error.value='';try{load(await window.aurabot.saveSettings(JSON.parse(JSON.stringify(draft.value)),[...clearSecrets.value]));}catch(e){error.value=e instanceof Error?e.message:t(displayLanguage.value,'saveFailed');revealAdvanced();}finally{working.value=false;}}
async function apply(force:boolean){if(dirty.value)return;working.value=true;error.value='';try{const result=await window.aurabot.applySettings(force);state.value=result.state;busy.value=result.busy;if(busy.value){await nextTick();cancelButton.value?.focus();}else await store.loadInitialState();}catch(e){error.value=e instanceof Error?e.message:t(displayLanguage.value,'applyFailed');busy.value=undefined;}finally{working.value=false;}}
async function debugAction(action:'open'|'clear'|'folder'){
 try{if(action==='open')await window.aurabot.openLiveDebugWindow();else if(action==='clear')await window.aurabot.clearLiveDebugLogs();else await window.aurabot.openLiveDebugLogDirectory();}
 catch(e){error.value=String(e);}
}
let unsubscribe:(()=>void)|undefined;
onMounted(async()=>{try{load(await window.aurabot.getSettings());unsubscribe=window.aurabot.onSettingsChanged(value=>{const hadDraft=dirty.value;state.value=value;if(!hadDraft)load(value);});}catch(e){error.value=String(e);}});
onBeforeUnmount(()=>unsubscribe?.());
</script>
<style scoped>
.debug-actions{display:flex;flex-wrap:wrap;gap:8px}
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
.settings-page-container{min-height:0;overflow:auto;flex:1;scrollbar-gutter:stable;padding-right:24px}
.settings-page-container>form{display:flex;flex-direction:column;min-height:100%}
.settings-page-section{padding:8px 0 24px;flex:1;min-width:0;width:100%;max-width:800px;overflow-wrap:anywhere}
.settings-model-card{padding:20px 0;border-bottom:1px solid var(--aura-border)}
.settings-workspace-add{display:flex;width:fit-content;margin-top:20px}
.settings-model-card h2,.settings-page-section h2{font-size:15px;font-weight:600;margin:0 0 16px}
.settings-page-field{display:grid;grid-template-columns:minmax(130px,30%) minmax(0,1fr);align-items:center;gap:16px;margin:16px 0}
.settings-page-field>span{line-height:1.6;overflow-wrap:anywhere}
.settings-page-field input,.settings-page-field select{min-width:0}
.settings-field-help{display:grid;grid-template-columns:minmax(130px,30%) minmax(0,1fr);gap:16px;margin:-8px 0 16px}
.settings-field-help>button{grid-column:2;justify-self:start;max-width:100%;min-height:28px;padding:2px 0;border:0;background:transparent;color:var(--aura-accent);font-size:12px;font-weight:400;text-align:left}
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
@media(max-width:800px){.settings-field-help{grid-template-columns:1fr;gap:8px}.settings-field-help>button{grid-column:1}}
@media(max-width:500px){.settings-v2-layout{display:flex;flex-direction:column}.settings-v2-sidebar{padding:12px;border-right:0;border-bottom:1px solid var(--aura-border);overflow:visible}.settings-v2-sidebar-brand{display:none}.settings-v2-navigation{display:flex;overflow:auto}.settings-v2-nav-item{width:auto;white-space:nowrap}.settings-v2-nav-item :deep(svg){display:none}.settings-v2-content{flex:1}}
</style>

<style scoped>
.settings-v2-content-header{display:flex;align-items:center;justify-content:flex-start;gap:12px}.profile-back{display:grid;place-items:center;width:36px;height:36px;padding:6px;-webkit-app-region:no-drag}.profile-back svg{width:22px;height:22px}
</style>
