<!--
  Console 主窗口：呈现主聊天消息与输入区。
  消息由 Pinia store 从主进程加载，输入通过现有聊天 IPC 发送；该组件不拥有
  Task 执行控制，只与通用聊天组件和玻璃面板组合。
-->
<template>
  <main class="console-window" :aria-label="t(store.displayLanguage, 'consoleTitle')">
    <button
      type="button"
      class="console-settings-button"
      :aria-label="t(store.displayLanguage, 'openAuraBotSettings')"
      :title="t(store.displayLanguage, 'settings')"
      @click="void store.openSettingsWindow()"
    >
      <Icon icon="solar:settings-linear" aria-hidden="true" />
    </button>
    <div class="console-body">
      <aside class="console-tasks" aria-label="Task 状态">
        <h2>任务</h2><p>通过与 Aura 对话来创建和管理任务。</p>
        <p v-if="taskError" role="status">{{ taskError }}</p>
        <p v-else-if="!tasks.length">暂无任务</p>
        <ul><li v-for="task in tasks" :key="task.id"><strong>{{ task.title }}</strong><span>{{ taskLabels[task.status] ?? task.status }}</span></li></ul>
      </aside>
      <section class="console-chat" :aria-label="t(store.displayLanguage, 'mainConversation')">
        <button v-if="store.historyCursor" type="button" @click="store.loadOlderHistory()">加载更早记录</button>
        <p v-if="store.chatError" role="alert">{{ store.chatError }}</p>
        <MessageList :messages="store.messages" :display-language="store.displayLanguage" />
        <ConsoleGlassPanel variant="control" class="console-composer-glass">
          <Composer
            :display-language="store.displayLanguage"
            :agent-status="store.agentStatus"
            :on-send="(content) => store.sendMessage(content, 'console')"
          />
        </ConsoleGlassPanel>
      </section>
    </div>
  </main>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue';
import type { TaskSummary } from '../../shared/contracts/desktop';
import { Icon, addCollection } from "@iconify/vue";
import solarIcons from "@iconify-json/solar/icons.json";
import { useAuraBotStore } from "../../app/store";
import { t } from "../../i18n";
import Composer from "../chat/Composer.vue";
import MessageList from "../chat/MessageList.vue";
import ConsoleGlassPanel from "./ConsoleGlassPanel.vue";

addCollection(solarIcons);
const store = useAuraBotStore();
const tasks=ref<TaskSummary[]>([]);const taskError=ref('');
const taskLabels:Record<string,string>={queued:'排队中',running:'执行中',waiting:'等待回答',paused:'已暂停',completed:'已完成',failed:'失败',cancelled:'已取消'};
let unsubscribe:(()=>void)|undefined;
function visibility(){void window.aurabot.subscribeTasks(!document.hidden);}
onMounted(()=>{unsubscribe=window.aurabot.onTasksChanged(value=>{tasks.value=value.tasks;taskError.value=value.error??'';});visibility();document.addEventListener('visibilitychange',visibility);});
onBeforeUnmount(()=>{unsubscribe?.();document.removeEventListener('visibilitychange',visibility);void window.aurabot.subscribeTasks(false);});
</script>

<style scoped>
.console-tasks{width:235px;flex-shrink:0;padding:24px 18px;overflow:auto;border-right:1px solid #b7aba330;font-size:13px}.console-tasks h2{font-size:18px}.console-tasks p{opacity:.65;line-height:1.6}.console-tasks ul{list-style:none;padding:0}.console-tasks li{padding:14px 0;border-bottom:1px solid #b7aba330;display:flex;flex-direction:column;gap:8px}.console-tasks span{font-size:12px;opacity:.65}@media(max-width:800px){.console-tasks{width:180px}}
</style>
