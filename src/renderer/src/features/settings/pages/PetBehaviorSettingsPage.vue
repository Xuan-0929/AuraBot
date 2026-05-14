<template>
  <section class="settings-page-section" :aria-label="t(store.displayLanguage, 'petBehavior')">
    <SettingsPageLead :title="t(store.displayLanguage, 'petBehavior')" :hint="t(store.displayLanguage, 'petBehaviorSettingsHint')" />
    <label class="settings-page-toggle">
      <input
        type="checkbox"
        :checked="store.petSettings.clickThroughPet"
        :aria-label="t(store.displayLanguage, 'clickThroughCharacter')"
        @change="void store.updatePetSettings({ clickThroughPet: ($event.currentTarget as HTMLInputElement).checked })"
      />
      <span>{{ t(store.displayLanguage, "clickThroughCharacter") }}</span>
    </label>
  </section>
</template>

<script setup lang="ts">
import { useAuraBotStore } from "../../../app/store";
import { t } from "../../../i18n";
import SettingsPageLead from "../SettingsPageLead.vue";

const store = useAuraBotStore();
</script>
