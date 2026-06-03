<template>
  <section class="settings-page-section" :aria-label="t(store.displayLanguage, 'modelSettings')">
    <div class="settings-page-section-header">
      <SettingsPageLead :title="t(store.displayLanguage, 'model')" :hint="t(store.displayLanguage, 'modelSettingsHint')" />
      <button type="button" class="settings-config-button" @click="void store.openConfigFile()">
        {{ t(store.displayLanguage, "openConfigFile") }}
      </button>
    </div>
    <div class="settings-page-model-list">
      <article v-for="row in modelRows" v-if="store.appConfig" :key="row.key" class="settings-model-card">
        <div class="settings-model-card-header">
          <strong>{{ t(store.displayLanguage, row.labelKey) }}</strong>
          <span>{{ store.appConfig[row.key].enabled ? t(store.displayLanguage, "enabled") : t(store.displayLanguage, "disabled") }}</span>
        </div>
        <div class="settings-model-card-actions">
          <div class="settings-model-health">
            <span class="settings-model-health-label">{{ t(store.displayLanguage, "modelHealth") }}</span>
            <span class="settings-model-health-value">{{ modelHealthLabel(store.modelHealth?.[row.key], store.displayLanguage) }}</span>
          </div>
          <button
            type="button"
            class="settings-model-test-button"
            :aria-label="testConnectionAriaLabel(t(store.displayLanguage, row.labelKey), store.displayLanguage)"
            :disabled="store.modelHealth?.[row.key]?.state === 'testing'"
            @click="void store.testModelConnection(row.key)"
          >
            {{ t(store.displayLanguage, "testConnection") }}
          </button>
        </div>
        <dl>
          <dt>{{ t(store.displayLanguage, "provider") }}</dt>
          <dd>
            <select
              class="settings-model-provider-select"
              :value="toModelProviderOption(store.appConfig[row.key].provider)"
              :aria-label="`${t(store.displayLanguage, row.labelKey)} ${t(store.displayLanguage, 'provider')}`"
              @change="void store.updateModelProviderSettings({ role: row.key, provider: ($event.currentTarget as HTMLSelectElement).value as ModelProviderOption })"
            >
              <option v-for="provider in modelProviderOptions" :key="provider" :value="provider">
                {{ getModelProviderLabel(provider, store.displayLanguage) }}
              </option>
            </select>
          </dd>
          <dt>{{ t(store.displayLanguage, "model") }}</dt>
          <dd>{{ store.appConfig[row.key].model }}</dd>
          <template v-if="store.appConfig[row.key].baseUrl">
            <dt>Base URL</dt>
            <dd>{{ store.appConfig[row.key].baseUrl }}</dd>
          </template>
          <dt>{{ t(store.displayLanguage, "apiKey") }}</dt>
          <dd>{{ store.appConfig[row.key].hasApiKey ? t(store.displayLanguage, "apiKeySaved") : t(store.displayLanguage, "notSet") }}</dd>
          <template v-if="store.modelHealth?.[row.key]?.lastErrorCode">
            <dt>{{ t(store.displayLanguage, "modelStateError") }}</dt>
            <dd>{{ modelHealthErrorLabel(store.modelHealth?.[row.key]?.lastErrorCode, store.displayLanguage) }}</dd>
          </template>
        </dl>
      </article>
      <p v-else class="settings-empty">{{ t(store.displayLanguage, "configNotLoaded") }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { MODEL_PROVIDER_OPTIONS as modelProviderOptions, type ModelProviderOption, type ModelRoleKey } from "@shared/contracts/settings";
import { useAuraBotStore } from "../../../app/store";
import { t, type UiTextKey } from "../../../i18n";
import {
  getModelProviderLabel,
  modelHealthErrorLabel,
  modelHealthLabel,
  testConnectionAriaLabel,
  toModelProviderOption
} from "../settingsHelpers";
import SettingsPageLead from "../SettingsPageLead.vue";

const store = useAuraBotStore();

const modelRows: Array<{ key: ModelRoleKey; labelKey: UiTextKey }> = [
  { key: "chatModel", labelKey: "chatModel" },
  { key: "gateModel", labelKey: "gateModel" },
  { key: "memoryModel", labelKey: "memoryModel" }
];
</script>
