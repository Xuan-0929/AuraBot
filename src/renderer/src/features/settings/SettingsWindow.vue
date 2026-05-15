<template>
  <main class="settings-window" :aria-label="t(store.displayLanguage, 'settings')">
    <div class="settings-window-shell">
      <header class="settings-window-header" :data-has-back="selectedSection ? 'true' : 'false'">
        <button
          v-if="selectedSection"
          type="button"
          class="settings-back-button"
          :aria-label="t(store.displayLanguage, 'backToSettings')"
          @click="goBack"
        >
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="m15 6-6 6 6 6" />
          </svg>
        </button>
        <span class="settings-window-kicker">{{ headerKicker }}</span>
        <h1>{{ headerTitle }}</h1>
        <p>{{ headerDescription }}</p>
      </header>
      <SettingsCenterShell
        v-if="!selectedSection"
        :display-language="store.displayLanguage"
        :items="sectionItems"
        watermark-icon="settings"
        @select="selectSection"
      />
      <SettingsCenterShell
        v-else-if="!currentPage"
        :display-language="store.displayLanguage"
        :items="secondaryItems"
        :watermark-icon="currentSection?.icon"
        @select="selectPage"
      />
      <section v-else class="settings-page-container" :aria-label="t(store.displayLanguage, 'settingsContent')">
        <GeneralSettingsPage v-if="currentPage === 'general'" />
        <PetBehaviorSettingsPage v-else-if="currentPage === 'petBehavior'" />
        <ModelSettingsPage v-else-if="currentPage === 'model'" />
        <TaskCliSettingsPage v-else-if="currentPage === 'taskCli'" />
        <PersonaSettingsPage v-else-if="currentPage === 'persona'" />
        <Live2DModelSettingsPage v-else-if="currentPage === 'live2dModel'" />
        <NotificationSettingsPage v-else-if="currentPage === 'notifications'" />
        <MemoryDebugSettingsPage v-else-if="currentPage === 'memoryDebug'" />
      </section>
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useAuraBotStore } from "../../app/store";
import { t, type UiTextKey } from "../../i18n";
import SettingsCenterShell, { type SettingsNavItem, type SettingsPageKey } from "./SettingsCenterShell.vue";
import type { SettingsNavIconName } from "./SettingsNavIcon.vue";
import GeneralSettingsPage from "./pages/GeneralSettingsPage.vue";
import Live2DModelSettingsPage from "./pages/Live2DModelSettingsPage.vue";
import MemoryDebugSettingsPage from "./pages/MemoryDebugSettingsPage.vue";
import ModelSettingsPage from "./pages/ModelSettingsPage.vue";
import NotificationSettingsPage from "./pages/NotificationSettingsPage.vue";
import PersonaSettingsPage from "./pages/PersonaSettingsPage.vue";
import PetBehaviorSettingsPage from "./pages/PetBehaviorSettingsPage.vue";
import TaskCliSettingsPage from "./pages/TaskCliSettingsPage.vue";

const store = useAuraBotStore();
type SettingsSectionKey = "basics" | "companion" | "intelligence" | "automation";

type SettingsPageDefinition = SettingsNavItem & {
  key: SettingsPageKey;
  labelKey: UiTextKey;
  descriptionKey: UiTextKey;
  icon: SettingsNavIconName;
};

type SettingsSectionDefinition = SettingsNavItem & {
  key: SettingsSectionKey;
  labelKey: UiTextKey;
  descriptionKey: UiTextKey;
  icon: SettingsNavIconName;
  pages: SettingsPageKey[];
};

const selectedSection = ref<SettingsSectionKey | undefined>();
const currentPage = ref<SettingsPageKey | undefined>();

const pageDefinitions = computed<Record<SettingsPageKey, SettingsPageDefinition>>(() => ({
  general: pageItem("general", "general", "generalSettingsHint", "general"),
  petBehavior: pageItem("petBehavior", "petBehavior", "petBehaviorSettingsHint", "pet"),
  model: pageItem("model", "model", "modelSettingsHint", "model"),
  taskCli: pageItem("taskCli", "taskCli", "taskCliHint", "task"),
  persona: pageItem("persona", "persona", "personaSettingsHint", "persona"),
  live2dModel: pageItem("live2dModel", "live2dModel", "live2dModelSettingsHint", "live2d"),
  notifications: pageItem("notifications", "notifications", "notificationSettingsHint", "notifications"),
  memoryDebug: pageItem("memoryDebug", "memoryDebug", "memoryDebugSettingsHint", "memory")
}));

const sectionItems = computed<SettingsSectionDefinition[]>(() => [
  sectionItem("basics", "settingsSectionBasics", "settingsSectionBasicsHint", "general", ["general", "notifications"]),
  sectionItem("companion", "settingsSectionCompanion", "settingsSectionCompanionHint", "pet", ["petBehavior", "persona", "live2dModel"]),
  sectionItem("intelligence", "settingsSectionIntelligence", "settingsSectionIntelligenceHint", "model", ["model", "memoryDebug"]),
  sectionItem("automation", "settingsSectionAutomation", "settingsSectionAutomationHint", "task", ["taskCli"])
]);

const currentSection = computed(() => sectionItems.value.find((item) => item.key === selectedSection.value));
const currentPageDefinition = computed(() => (currentPage.value ? pageDefinitions.value[currentPage.value] : undefined));
const secondaryItems = computed<SettingsNavItem[]>(() => currentSection.value?.pages.map((key) => pageDefinitions.value[key]) ?? []);
const headerKicker = computed(() => {
  if (currentPageDefinition.value) {
    return currentSection.value?.label ?? t(store.displayLanguage, "settings");
  }
  if (currentSection.value) {
    return t(store.displayLanguage, "settings");
  }
  return t(store.displayLanguage, "settingsWindowEyebrow");
});
const headerTitle = computed(() => currentPageDefinition.value?.label ?? currentSection.value?.label ?? t(store.displayLanguage, "settings"));
const headerDescription = computed(
  () => currentPageDefinition.value?.description ?? currentSection.value?.description ?? t(store.displayLanguage, "productSettingsSubtitle")
);

function pageItem(
  key: SettingsPageKey,
  labelKey: UiTextKey,
  descriptionKey: UiTextKey,
  icon: SettingsNavIconName
): SettingsPageDefinition {
  return {
    key,
    labelKey,
    descriptionKey,
    icon,
    label: t(store.displayLanguage, labelKey),
    description: t(store.displayLanguage, descriptionKey)
  };
}

function sectionItem(
  key: SettingsSectionKey,
  labelKey: UiTextKey,
  descriptionKey: UiTextKey,
  icon: SettingsNavIconName,
  pages: SettingsPageKey[]
): SettingsSectionDefinition {
  return {
    key,
    labelKey,
    descriptionKey,
    icon,
    pages,
    label: t(store.displayLanguage, labelKey),
    description: t(store.displayLanguage, descriptionKey)
  };
}

function selectSection(key: string) {
  selectedSection.value = key as SettingsSectionKey;
  currentPage.value = undefined;
}

function selectPage(key: string) {
  currentPage.value = key as SettingsPageKey;
}

function goBack() {
  if (currentPage.value) {
    currentPage.value = undefined;
    return;
  }
  selectedSection.value = undefined;
}
</script>
