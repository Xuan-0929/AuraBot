<template>
  <div class="settings-center">
    <aside class="settings-center-sidebar" :aria-label="t(displayLanguage, 'settingsNavigation')">
      <p class="settings-center-sidebar-note">{{ t(displayLanguage, "settingsSidebarHint") }}</p>
      <nav class="settings-center-nav" role="tablist" :aria-label="t(displayLanguage, 'settingsNavigation')">
        <button
          v-for="item in items"
          :key="item.key"
          type="button"
          role="tab"
          :aria-label="item.label"
          :aria-selected="currentPage === item.key"
          class="settings-center-tab"
          :data-active="currentPage === item.key"
          @click="emit('select', item.key)"
        >
          {{ item.label }}
        </button>
      </nav>
    </aside>
    <section class="settings-center-content" :aria-label="t(displayLanguage, 'settingsContent')">
      <slot />
    </section>
  </div>
</template>

<script setup lang="ts">
import type { PetSettings } from "../../../../shared/schemas/petSettings";
import { t } from "../../i18n";

export type SettingsPageKey =
  | "general"
  | "petBehavior"
  | "model"
  | "taskCli"
  | "persona"
  | "live2dModel"
  | "notifications"
  | "memoryDebug";

export interface SettingsNavItem {
  key: SettingsPageKey;
  label: string;
}

defineProps<{
  displayLanguage: PetSettings["displayLanguage"];
  currentPage: SettingsPageKey;
  items: SettingsNavItem[];
}>();

const emit = defineEmits<{
  select: [SettingsPageKey];
}>();
</script>
