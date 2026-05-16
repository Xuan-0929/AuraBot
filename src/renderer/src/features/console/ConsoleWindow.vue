<template>
  <main class="console-window" :aria-label="t(store.displayLanguage, 'consoleTitle')">
    <header class="console-header">
      <div class="console-header-copy">
        <div class="console-header-title-row">
          <h1>{{ t(store.displayLanguage, "consoleDashboardTitle") }}</h1>
          <ConsoleGlassPanel variant="pill" class="console-status-glass">
            <AgentStatusBar :status="store.agentStatus" :display-language="store.displayLanguage" />
          </ConsoleGlassPanel>
        </div>
        <p>{{ t(store.displayLanguage, "consoleSubtitle") }}</p>
      </div>
      <aside class="console-task-sticky-layer" :aria-label="t(store.displayLanguage, 'taskStickyNotes')">
        <header class="console-task-panel-meta">
          <strong>{{ t(store.displayLanguage, "consoleTaskViewportTitle") }}</strong>
          <button
            type="button"
            class="console-task-settings-button"
            :aria-label="t(store.displayLanguage, 'openAuraBotSettings')"
            :title="t(store.displayLanguage, 'settings')"
            @click="void store.openSettingsWindow()"
          >
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z" />
              <path d="M19.4 13.5a7.8 7.8 0 0 0 .1-1.5 7.8 7.8 0 0 0-.1-1.5l2-1.5-2-3.4-2.4 1a8.1 8.1 0 0 0-2.6-1.5L14 2.5h-4l-.4 2.6A8.1 8.1 0 0 0 7 6.6l-2.4-1-2 3.4 2 1.5a7.8 7.8 0 0 0-.1 1.5 7.8 7.8 0 0 0 .1 1.5l-2 1.5 2 3.4 2.4-1a8.1 8.1 0 0 0 2.6 1.5l.4 2.6h4l.4-2.6a8.1 8.1 0 0 0 2.6-1.5l2.4 1 2-3.4-2-1.5Z" />
            </svg>
          </button>
        </header>
        <ConsoleGlassPanel class="console-task-glass">
          <div class="console-task-viewport">
            <div class="console-task-viewport-list">
              <TaskStickyNotes
                v-if="consoleTasks.length > 0"
                :tasks="consoleTasks"
                variant="console"
                :show-header="false"
                :aria-label="false"
                :display-language="store.displayLanguage"
                :on-cancel-task="(taskId) => void store.cancelTask(taskId)"
                :on-delete-task="(taskId) => void store.deleteTask(taskId)"
                :on-view-task-detail="(taskId) => void store.openTaskDetail(taskId)"
              />
              <div v-else class="console-task-viewport-empty" aria-hidden="true" />
            </div>
          </div>
        </ConsoleGlassPanel>
      </aside>
    </header>
    <div class="console-body">
      <section class="console-chat" :aria-label="t(store.displayLanguage, 'mainConversation')">
        <MessageList :messages="store.messages" :display-language="store.displayLanguage" />
        <ConsoleGlassPanel variant="control" class="console-composer-glass">
          <Composer :display-language="store.displayLanguage" :on-send="(content) => store.sendMessage(content)" />
        </ConsoleGlassPanel>
      </section>
      <TaskDetailPanel
        v-if="store.selectedTaskDetail"
        :detail="store.selectedTaskDetail"
        :display-language="store.displayLanguage"
        :on-close="() => store.closeTaskDetail()"
      />
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useAuraBotStore } from "../../app/store";
import { t } from "../../i18n";
import Composer from "../chat/Composer.vue";
import MessageList from "../chat/MessageList.vue";
import AgentStatusBar from "../status/AgentStatusBar.vue";
import TaskDetailPanel from "../tasks/TaskDetailPanel.vue";
import TaskStickyNotes from "../tasks/TaskStickyNotes.vue";
import { pickConsoleStickyTasks } from "../tasks/taskStickyNotes";
import ConsoleGlassPanel from "./ConsoleGlassPanel.vue";

const store = useAuraBotStore();
const consoleTasks = computed(() => pickConsoleStickyTasks(store.tasks));
</script>
