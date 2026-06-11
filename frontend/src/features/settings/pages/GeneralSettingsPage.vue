<template>
  <section class="settings-page-section" :aria-label="t(store.displayLanguage, 'general')">
    <SettingsPageLead :title="t(store.displayLanguage, 'general')" :hint="t(store.displayLanguage, 'generalSettingsHint')" />
    <label class="settings-page-field">
      <span>{{ t(store.displayLanguage, "language") }}</span>
      <select
        :value="store.petSettings.displayLanguage"
        :aria-label="t(store.displayLanguage, 'language')"
        @change="void store.updatePetSettings({ displayLanguage: ($event.currentTarget as HTMLSelectElement).value as DisplayLanguage })"
      >
        <option value="en">{{ t(store.displayLanguage, "english") }}</option>
        <option value="zh">{{ t(store.displayLanguage, "chinese") }}</option>
        <option value="ja">{{ t(store.displayLanguage, "japanese") }}</option>
      </select>
    </label>
  </section>
</template>

<script setup lang="ts">
import { useAuraBotStore } from "../../../app/store";
import { t, type DisplayLanguage } from "../../../i18n";
import SettingsPageLead from "../SettingsPageLead.vue";

const store = useAuraBotStore();
</script>
