<template>
  <main class="settings-window" :aria-label="t(store.displayLanguage, 'settings')">
    <header class="settings-window-header">
      <div class="settings-window-copy">
        <span class="settings-window-kicker">{{ t(store.displayLanguage, "settingsWindowEyebrow") }}</span>
        <h1>{{ t(store.displayLanguage, "settings") }}</h1>
        <p>{{ t(store.displayLanguage, "productSettingsSubtitle") }}</p>
      </div>
    </header>
    <SettingsCenterShell
      :display-language="store.displayLanguage"
      :current-page="currentPage"
      :items="navItems"
      @select="currentPage = $event"
    >
      <GeneralSettingsPage v-if="currentPage === 'general'" />
      <PetBehaviorSettingsPage v-else-if="currentPage === 'petBehavior'" />
      <ModelSettingsPage v-else-if="currentPage === 'model'" />
      <TaskCliSettingsPage v-else-if="currentPage === 'taskCli'" />
      <PersonaSettingsPage v-else-if="currentPage === 'persona'" />
      <Live2DModelSettingsPage v-else-if="currentPage === 'live2dModel'" />
      <NotificationSettingsPage v-else-if="currentPage === 'notifications'" />
      <MemoryDebugSettingsPage v-else-if="currentPage === 'memoryDebug'" />
    </SettingsCenterShell>
  </main>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useAuraBotStore } from "../../app/store";
import { t } from "../../i18n";
import SettingsCenterShell, { type SettingsNavItem, type SettingsPageKey } from "./SettingsCenterShell.vue";
import GeneralSettingsPage from "./pages/GeneralSettingsPage.vue";
import Live2DModelSettingsPage from "./pages/Live2DModelSettingsPage.vue";
import MemoryDebugSettingsPage from "./pages/MemoryDebugSettingsPage.vue";
import ModelSettingsPage from "./pages/ModelSettingsPage.vue";
import NotificationSettingsPage from "./pages/NotificationSettingsPage.vue";
import PersonaSettingsPage from "./pages/PersonaSettingsPage.vue";
import PetBehaviorSettingsPage from "./pages/PetBehaviorSettingsPage.vue";
import TaskCliSettingsPage from "./pages/TaskCliSettingsPage.vue";

const store = useAuraBotStore();
const currentPage = ref<SettingsPageKey>("general");
const navItems = computed<SettingsNavItem[]>(() => [
  { key: "general", label: t(store.displayLanguage, "general") },
  { key: "petBehavior", label: t(store.displayLanguage, "petBehavior") },
  { key: "model", label: t(store.displayLanguage, "model") },
  { key: "taskCli", label: t(store.displayLanguage, "taskCli") },
  { key: "persona", label: t(store.displayLanguage, "persona") },
  { key: "live2dModel", label: t(store.displayLanguage, "live2dModel") },
  { key: "notifications", label: t(store.displayLanguage, "notifications") },
  { key: "memoryDebug", label: t(store.displayLanguage, "memoryDebug") }
]);
</script>
