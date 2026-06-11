<template>
  <section class="settings-page-section" :aria-label="t(store.displayLanguage, 'live2dModel')">
    <div class="settings-page-section-header">
      <SettingsPageLead :title="t(store.displayLanguage, 'live2dModel')" :hint="t(store.displayLanguage, 'live2dModelSettingsHint')" />
    </div>
    <div v-if="currentModel" class="settings-live2d-layout">
      <div class="settings-live2d-preview-card">
        <div class="settings-live2d-preview">
          <Live2DPreview :model-path="store.live2dModelState?.currentModelPath ?? currentModel.manifestPath" />
        </div>
        <dl class="settings-live2d-meta">
          <dt>{{ t(store.displayLanguage, "live2dCurrentModel") }}</dt>
          <dd>{{ currentModel.displayName }}</dd>
          <dt>{{ t(store.displayLanguage, "live2dModelPath") }}</dt>
          <dd>{{ currentModel.manifestPath }}</dd>
        </dl>
      </div>
      <div class="settings-live2d-list">
        <h3>{{ t(store.displayLanguage, "live2dLocalModels") }}</h3>
        <button
          v-for="model in store.live2dModelState?.models ?? []"
          :key="model.id"
          type="button"
          class="settings-live2d-model-item"
          :data-active="model.id === store.live2dModelState?.currentModelId"
          :aria-label="getSwitchModelLabel(model.displayName)"
          @click="void store.setCurrentLive2DModel(model.id)"
        >
          <strong>{{ model.displayName }}</strong>
          <span>{{ model.manifestPath }}</span>
        </button>
      </div>
    </div>
    <p v-else class="settings-empty">{{ t(store.displayLanguage, "live2dModelSettingsComingSoon") }}</p>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useAuraBotStore } from "../../../app/store";
import { t } from "../../../i18n";
import Live2DPreview from "../../live2d/Live2DPreview.vue";
import SettingsPageLead from "../SettingsPageLead.vue";

const store = useAuraBotStore();
const currentModel = computed(() =>
  store.live2dModelState?.models.find((model) => model.id === store.live2dModelState?.currentModelId)
);

function getSwitchModelLabel(modelName: string): string {
  if (store.displayLanguage === "zh") {
    return `切换到 ${modelName} 模型`;
  }

  if (store.displayLanguage === "ja") {
    return `${modelName} モデルに切り替え`;
  }

  return `Switch to ${modelName} model`;
}
</script>
