<template>
  <section class="settings-page-section" :aria-label="t(store.displayLanguage, 'memoryDebug')">
    <div class="settings-page-section-header">
      <SettingsPageLead :title="t(store.displayLanguage, 'memoryDebug')" :hint="t(store.displayLanguage, 'memoryDebugSettingsHint')" />
      <button type="button" class="settings-config-button" :aria-label="t(store.displayLanguage, 'refresh')" @click="void store.loadMemoryDebugSnapshot()">
        {{ t(store.displayLanguage, "refresh") }}
      </button>
    </div>
    <div class="memory-debug-panel memory-debug-panel-page" :aria-label="t(store.displayLanguage, 'memoryDebugPanel')">
      <MemoryDebugContent :snapshot="store.memoryDebugSnapshot" :display-language="store.displayLanguage" />
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted } from "vue";
import { useAuraBotStore } from "../../../app/store";
import { t } from "../../../i18n";
import MemoryDebugContent from "../MemoryDebugContent.vue";
import SettingsPageLead from "../SettingsPageLead.vue";

const store = useAuraBotStore();

onMounted(() => {
  void store.loadMemoryDebugSnapshot();
});
</script>
