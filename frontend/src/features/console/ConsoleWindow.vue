<!--
  主聊天窗口：以稳定的标题栏、可收起任务列表和连续对话组织现有聊天功能。
  Pinia 负责消息与设置入口，任务由既有 IPC 推送；侧栏显隐仅属于窗口内的展示状态，
  标题复用 i18n 三语文案，任务状态使用无外圈 SVG；Vue Transition 配合共享 CSS 横向收展任务面板。
  窄窗口使用带焦点管理的覆盖面板，不改变任务订阅或执行生命周期。
-->
<template>
  <main class="console-window" :aria-label="t(store.displayLanguage, 'consoleTitle')">
    <header class="console-header" :inert="overlayOpen ? true : undefined">
      <div class="console-brand">
        <button ref="sidebarToggle" type="button" class="icon-button" aria-controls="console-tasks" :aria-expanded="sidebarOpen" :aria-label="t(store.displayLanguage, sidebarOpen ? 'taskSidebarCollapse' : 'taskSidebarExpand')" :title="t(store.displayLanguage, sidebarOpen ? 'taskSidebarCollapse' : 'taskSidebarExpand')" @click="toggleSidebar">
          <Icon icon="solar:sidebar-minimalistic-linear" aria-hidden="true" />
        </button>
        <span class="aura-wordmark">{{ t(store.displayLanguage, 'chat') }}</span>
      </div>
      <button type="button" class="icon-button console-settings-button" :aria-label="t(store.displayLanguage, 'openAuraBotSettings')" :title="t(store.displayLanguage, 'settings')" @click="void store.openSettingsWindow()">
        <Icon icon="solar:settings-linear" aria-hidden="true" />
      </button>
    </header>
    <div class="console-body">
      <Transition name="console-scrim">
        <div v-if="overlayOpen" class="console-sidebar-scrim" aria-hidden="true" @click="closeSidebar" />
      </Transition>
      <Transition name="console-sidebar">
      <aside v-show="sidebarOpen" id="console-tasks" ref="sidebar" class="console-tasks" :class="{'console-tasks-overlay': narrow}" :inert="!sidebarOpen ? true : undefined" :aria-hidden="!sidebarOpen ? true : undefined" :role="overlayOpen ? 'dialog' : undefined" :aria-modal="overlayOpen ? true : undefined" aria-labelledby="tasks-heading" tabindex="-1" @keydown="handleSidebarKeydown">
        <div class="console-tasks-heading"><h2 id="tasks-heading">{{ t(store.displayLanguage, 'taskList') }}</h2><button v-if="narrow" type="button" class="icon-button" :aria-label="t(store.displayLanguage, 'closeTaskList')" :title="t(store.displayLanguage, 'closeTaskList')" @click="closeSidebar"><Icon icon="solar:close-circle-linear" aria-hidden="true" /></button></div>
        <p v-if="taskError" role="status" class="inline-error">{{ taskError }}</p>
        <p v-else-if="!tasks.length" class="task-empty">{{ t(store.displayLanguage, 'noTasks') }}</p>
        <ul class="task-list"><li v-for="task in tasks" :key="task.id"><strong>{{ task.title }}</strong><span class="task-status" :data-status="task.status"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="taskPaths[task.status] ?? taskPaths.queued" /></svg>{{ taskLabel(task.status) }}</span></li></ul>
      </aside>
      </Transition>
      <section class="console-chat" :inert="overlayOpen ? true : undefined" :aria-label="t(store.displayLanguage, 'mainConversation')">
        <div v-if="store.historyCursor || store.chatError" class="console-chat-notices">
          <button v-if="store.historyCursor" type="button" class="history-button" @click="store.loadOlderHistory()"><Icon icon="solar:history-linear" aria-hidden="true" />{{ t(store.displayLanguage, 'loadEarlierHistory') }}</button>
          <p v-if="store.chatError" role="alert" class="inline-error">{{ store.chatError }}</p>
        </div>
        <MessageList :messages="store.messages" :display-language="store.displayLanguage" />
        <div class="console-composer"><Composer :display-language="store.displayLanguage" :agent-status="store.agentStatus" :on-send="(content) => store.sendMessage(content, 'console')" /></div>
      </section>
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, ref } from 'vue';
import { Icon, addCollection } from '@iconify/vue';
import solarIcons from '@iconify-json/solar/icons.json';
import type { TaskSummary } from '../../shared/contracts/desktop';
import { useAuraBotStore } from '../../app/store';
import { t } from '../../i18n';
import Composer from '../chat/Composer.vue';
import MessageList from '../chat/MessageList.vue';

addCollection(solarIcons);
const store = useAuraBotStore();
const tasks = ref<TaskSummary[]>([]);
const taskError = ref('');
const media = window.matchMedia('(max-width: 959px)');
const narrow = ref(media.matches);
const sidebarOpen = ref(!media.matches);
let wideSidebarOpen = true;
const overlayOpen = computed(() => narrow.value && sidebarOpen.value);
const sidebarToggle = ref<HTMLButtonElement>();
const sidebar = ref<HTMLElement>();
const taskLabelKeys = {queued:'taskQueued', running:'taskRunning', waiting:'taskWaiting', paused:'taskPaused', completed:'taskCompleted', failed:'taskFailed', cancelled:'taskCancelled'} as const;
const taskPaths: Record<string, string> = {
  queued: 'M6 3h12M6 21h12M7 3v4l10 10v4M17 3v4L7 17v4',
  running: 'M7 4l14 8-14 8Z',
  waiting: 'M9 8a3 3 0 0 1 6 0c0 3-3 3-3 6M12 19h.01',
  paused: 'M8 5v14M16 5v14',
  completed: 'M5 12l4 4L19 6',
  failed: 'M12 4v10M12 20h.01',
  cancelled: 'M6 6l12 12M18 6 6 18'
};
let unsubscribe: (() => void) | undefined;
function taskLabel(status: string) { const key = taskLabelKeys[status as keyof typeof taskLabelKeys]; return key ? t(store.displayLanguage, key) : status; }

async function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value;
  if (!narrow.value) wideSidebarOpen = sidebarOpen.value;
  if (overlayOpen.value) {
    await nextTick();
    sidebar.value?.querySelector<HTMLButtonElement>('button')?.focus();
  }
}
async function closeSidebar() {
  sidebarOpen.value = false;
  await nextTick();
  sidebarToggle.value?.focus();
}
function handleSidebarKeydown(event: KeyboardEvent) {
  if (!overlayOpen.value) return;
  if (event.key === 'Escape') { event.preventDefault(); void closeSidebar(); }
  if (event.key === 'Tab') {
    // The task list is read-only; its close button is the sole focusable control.
    event.preventDefault();
    sidebar.value?.querySelector<HTMLButtonElement>('button')?.focus();
  }
}
function resizeSidebar(event: MediaQueryListEvent) {
  const focusWasInside = sidebar.value?.contains(document.activeElement);
  narrow.value = event.matches;
  sidebarOpen.value = event.matches ? false : wideSidebarOpen;
  if (focusWasInside && event.matches) void nextTick(() => sidebarToggle.value?.focus());
}
function visibility() { void window.aurabot.subscribeTasks(!document.hidden); }
onMounted(() => {
  unsubscribe = window.aurabot.onTasksChanged(value => { tasks.value = value.tasks; taskError.value = value.error ?? ''; });
  visibility();
  document.addEventListener('visibilitychange', visibility);
  media.addEventListener('change', resizeSidebar);
});
onBeforeUnmount(() => {
  unsubscribe?.();
  document.removeEventListener('visibilitychange', visibility);
  media.removeEventListener('change', resizeSidebar);
  void window.aurabot.subscribeTasks(false);
});
</script>
