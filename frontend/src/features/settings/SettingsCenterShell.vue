<template>
  <div class="settings-center">
    <nav class="settings-center-nav" :aria-label="t(displayLanguage, 'settingsNavigation')">
      <button
        v-for="item in items"
        :key="item.key"
        type="button"
        :aria-label="item.label"
        class="settings-center-tab"
        @click="emit('select', item.key)"
      >
        <span class="settings-center-tab-copy">
          <span class="settings-center-tab-title">{{ item.label }}</span>
          <span class="settings-center-tab-description">{{ item.description }}</span>
        </span>
        <SettingsNavIcon :name="item.icon" />
      </button>
    </nav>
    <div v-if="watermarkIcon" class="settings-menu-watermark" aria-hidden="true">
      <SettingsNavIcon :name="watermarkIcon" />
    </div>
  </div>
</template>

<script setup lang="ts">
import type { PetSettings } from "@shared/schemas/petSettings";
import { t } from "../../i18n";
import SettingsNavIcon, { type SettingsNavIconName } from "./SettingsNavIcon.vue";

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
  key: string;
  label: string;
  description: string;
  icon: SettingsNavIconName;
}

defineProps<{
  displayLanguage: PetSettings["displayLanguage"];
  items: SettingsNavItem[];
  watermarkIcon?: SettingsNavIconName;
}>();

const emit = defineEmits<{
  select: [string];
}>();
</script>
