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
          <span>
            {{
              consoleTasks.length > 0
                ? t(store.displayLanguage, "consoleTaskViewportActive")
                : t(store.displayLanguage, "consoleTaskViewportIdle")
            }}
          </span>
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
import TaskStickyNotes from "../tasks/TaskStickyNotes.vue";
import { pickConsoleStickyTasks } from "../tasks/taskStickyNotes";
import ConsoleGlassPanel from "./ConsoleGlassPanel.vue";

const store = useAuraBotStore();
const consoleTasks = computed(() => pickConsoleStickyTasks(store.tasks));
</script>
