<!-- Edits the shared settings draft's persona fields. Save and Apply belong to SettingsWindow;
     no profile writes, reconnects or runtime changes occur while this form is edited. -->
<template>
  <section class="settings-page-section" :aria-label="t(store.displayLanguage, 'persona')">
    <div class="settings-page-section-header">
      <SettingsPageLead :title="t(store.displayLanguage, 'persona')" :hint="t(store.displayLanguage, 'personaSettingsHint')" />
      <button
        type="button"
        class="settings-config-button"
        :aria-label="showAdvanced ? t(store.displayLanguage, 'closeAdvancedPersonaEditing') : t(store.displayLanguage, 'openAdvancedPersonaEditing')"
        @click="showAdvanced = !showAdvanced"
      >
        {{ showAdvanced ? t(store.displayLanguage, "closeAdvancedPersonaEditing") : t(store.displayLanguage, "openAdvancedPersonaEditing") }}
      </button>
    </div>

    <label class="settings-page-field">
      <span>{{ t(store.displayLanguage, "personaName") }}</span>
      <input v-model="draft.name" type="text" :aria-label="t(store.displayLanguage, 'personaName')" />
    </label>
    <label class="settings-page-field">
      <span>{{ t(store.displayLanguage, "personaDescription") }}</span>
      <textarea v-model="draft.shortPersonaDescription" :aria-label="t(store.displayLanguage, 'personaDescription')" />
    </label>
    <label class="settings-page-field">
      <span>{{ t(store.displayLanguage, "personaTone") }}</span>
      <input v-model="draft.tone" type="text" :aria-label="t(store.displayLanguage, 'personaTone')" />
    </label>
    <label class="settings-page-field">
      <span>{{ t(store.displayLanguage, "personaRelationship") }}</span>
      <input v-model="draft.relationshipStyle" type="text" :aria-label="t(store.displayLanguage, 'personaRelationship')" />
    </label>
    <label class="settings-page-field">
      <span>{{ t(store.displayLanguage, "personaProactiveLevel") }}</span>
      <select v-model="draft.proactiveLevel" :aria-label="t(store.displayLanguage, 'personaProactiveLevel')">
        <option value="low">{{ t(store.displayLanguage, "personaProactiveLow") }}</option>
        <option value="balanced">{{ t(store.displayLanguage, "personaProactiveBalanced") }}</option>
        <option value="high">{{ t(store.displayLanguage, "personaProactiveHigh") }}</option>
      </select>
    </label>

    <div v-if="showAdvanced" class="settings-persona-advanced">
      <label class="settings-page-field">
        <span>{{ t(store.displayLanguage, "personaPrinciples") }}</span>
        <textarea :value="draft.principles.join('\n')" :aria-label="t(store.displayLanguage, 'personaPrinciples')" @input="draft.principles = splitLines(($event.currentTarget as HTMLTextAreaElement).value)" />
      </label>
      <label class="settings-page-field">
        <span>{{ t(store.displayLanguage, "personaTaboos") }}</span>
        <textarea :value="draft.taboos.join('\n')" :aria-label="t(store.displayLanguage, 'personaTaboos')" @input="draft.taboos = splitLines(($event.currentTarget as HTMLTextAreaElement).value)" />
      </label>
      <label class="settings-page-field">
        <span>{{ t(store.displayLanguage, "personaReminderStyle") }}</span>
        <textarea v-model="draft.reminderStyle" :aria-label="t(store.displayLanguage, 'personaReminderStyle')" />
      </label>
      <label class="settings-page-field">
        <span>{{ t(store.displayLanguage, "personaReplyPreference") }}</span>
        <textarea v-model="draft.replyPreference" :aria-label="t(store.displayLanguage, 'personaReplyPreference')" />
      </label>
    </div>

  </section>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { type PersonaSettings } from "@shared/contracts/personaSettings";
import { useAuraBotStore } from "../../../app/store";
import { t } from "../../../i18n";
import { splitLines } from "../settingsHelpers";
import SettingsPageLead from "../SettingsPageLead.vue";

const store = useAuraBotStore();
const showAdvanced = ref(false);
const draft = defineModel<PersonaSettings>({required:true});
</script>
