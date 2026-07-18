<template>
  <main class="settings-window" :aria-label="t(store.displayLanguage, 'settings')">
    <div class="settings-window-shell">
      <template v-if="isCompact">
        <header class="settings-window-header" :data-has-back="compactSection ? 'true' : 'false'">
          <button
            v-if="compactSection"
            type="button"
            class="settings-back-button"
            :aria-label="t(store.displayLanguage, 'backToSettings')"
            @click="goCompactBack"
          >
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="m15 6-6 6 6 6" />
            </svg>
          </button>
          <span class="settings-window-kicker">{{ compactHeaderKicker }}</span>
          <h1>{{ compactHeaderTitle }}</h1>
          <p>{{ compactHeaderDescription }}</p>
        </header>
        <SettingsCenterShell
          v-if="!compactSection"
          :display-language="store.displayLanguage"
          :items="sectionItems"
          watermark-icon="settings"
          @select="selectCompactSection"
        />
        <SettingsCenterShell
          v-else-if="!compactPage"
          :display-language="store.displayLanguage"
          :items="compactSecondaryItems"
          :watermark-icon="compactCurrentSection?.icon"
          @select="selectCompactPage"
        />
        <section v-else class="settings-page-container" :aria-label="t(store.displayLanguage, 'settingsContent')">
          <component :is="currentPageComponent" />
        </section>
      </template>

      <div v-else class="settings-v2-layout">
        <aside class="settings-v2-sidebar">
          <div class="settings-v2-sidebar-brand">AuraBot</div>
          <nav class="settings-v2-navigation" :aria-label="t(store.displayLanguage, 'settingsNavigation')">
            <section v-for="section in sectionItems" :key="section.key" class="settings-v2-nav-section">
              <h2>{{ section.label }}</h2>
              <button
                v-for="pageKey in section.pages"
                :key="pageKey"
                type="button"
                class="settings-v2-nav-item"
                :aria-current="currentPage === pageKey ? 'page' : undefined"
                @click="currentPage = pageKey"
              >
                <SettingsNavIcon :name="pageDefinitions[pageKey].icon" />
                <span>{{ pageDefinitions[pageKey].label }}</span>
              </button>
            </section>
          </nav>
        </aside>

        <section class="settings-v2-content" :aria-label="t(store.displayLanguage, 'settingsContent')">
          <Transition name="settings-page" mode="out-in">
            <div :key="currentPage" class="settings-page-transition">
              <header class="settings-v2-content-header">
                <div>
                  <span>{{ currentSection?.label }}</span>
                  <h1>{{ currentPageDefinition.label }}</h1>
                  <p>{{ currentPageDefinition.description }}</p>
                </div>
              </header>
              <section class="settings-page-container">
                <component :is="currentPageComponent" />
              </section>
            </div>
          </Transition>
        </section>
      </div>
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, type Component } from "vue";
import { useAuraBotStore } from "../../app/store";
import { t, type UiTextKey } from "../../i18n";
import SettingsCenterShell, { type SettingsNavItem, type SettingsPageKey } from "./SettingsCenterShell.vue";
import SettingsNavIcon, { type SettingsNavIconName } from "./SettingsNavIcon.vue";
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

const isCompact = ref(false);
const currentPage = ref<SettingsPageKey>("general");
const compactSection = ref<SettingsSectionKey | undefined>();
const compactPage = ref<SettingsPageKey | undefined>();
let compactMediaQuery: MediaQueryList | undefined;

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

const currentSection = computed(() => sectionItems.value.find((section) => section.pages.includes(currentPage.value)));
const currentPageDefinition = computed(() => pageDefinitions.value[currentPage.value]);
const compactCurrentSection = computed(() => sectionItems.value.find((section) => section.key === compactSection.value));
const compactPageDefinition = computed(() => (compactPage.value ? pageDefinitions.value[compactPage.value] : undefined));
const compactSecondaryItems = computed<SettingsNavItem[]>(() => compactCurrentSection.value?.pages.map((key) => pageDefinitions.value[key]) ?? []);
const compactHeaderKicker = computed(() => {
  if (compactPageDefinition.value) {
    return compactCurrentSection.value?.label ?? t(store.displayLanguage, "settings");
  }
  if (compactCurrentSection.value) {
    return t(store.displayLanguage, "settings");
  }
  return t(store.displayLanguage, "settingsWindowEyebrow");
});
const compactHeaderTitle = computed(
  () => compactPageDefinition.value?.label ?? compactCurrentSection.value?.label ?? t(store.displayLanguage, "settings")
);
const compactHeaderDescription = computed(
  () => compactPageDefinition.value?.description ?? compactCurrentSection.value?.description ?? t(store.displayLanguage, "productSettingsSubtitle")
);

const pageComponents: Record<SettingsPageKey, Component> = {
  general: GeneralSettingsPage,
  petBehavior: PetBehaviorSettingsPage,
  model: ModelSettingsPage,
  taskCli: TaskCliSettingsPage,
  persona: PersonaSettingsPage,
  live2dModel: Live2DModelSettingsPage,
  notifications: NotificationSettingsPage,
  memoryDebug: MemoryDebugSettingsPage
};
const currentPageComponent = computed(() => pageComponents[isCompact.value ? compactPage.value ?? currentPage.value : currentPage.value]);

onMounted(() => {
  compactMediaQuery = window.matchMedia("(max-width: 960px)");
  updateCompactLayout(compactMediaQuery);
  compactMediaQuery.addEventListener("change", updateCompactLayout);
});

onBeforeUnmount(() => compactMediaQuery?.removeEventListener("change", updateCompactLayout));

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

function selectCompactSection(key: string) {
  compactSection.value = key as SettingsSectionKey;
  compactPage.value = undefined;
}

function selectCompactPage(key: string) {
  const nextPage = key as SettingsPageKey;
  compactPage.value = nextPage;
  currentPage.value = nextPage;
}

function goCompactBack() {
  if (compactPage.value) {
    compactPage.value = undefined;
    return;
  }
  compactSection.value = undefined;
}

function updateCompactLayout(event: MediaQueryListEvent | MediaQueryList) {
  isCompact.value = event.matches;
}
</script>
