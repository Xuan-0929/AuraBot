<!--
  展示 Python Backend 状态并承载 active Memory 与公开 job 管理面。

  页面经 Store 调用 browse/forget/retry IPC；Backend 错误保持可见，不再呈现旧图谱、
  已删除的服务注册表、Task runtime 或内部提取数据。
-->
<template>
  <section class="settings-page-section" :aria-label="t(store.displayLanguage, 'memoryDebug')">
    <div class="settings-page-section-header">
      <SettingsPageLead :title="t(store.displayLanguage, 'memoryDebug')" :hint="t(store.displayLanguage, 'memoryDebugSettingsHint')" />
      <button type="button" class="settings-config-button" :aria-label="t(store.displayLanguage, 'refresh')" @click="void refreshDebugState()">
        {{ t(store.displayLanguage, "refresh") }}
      </button>
    </div>
    <section
      class="backend-status-panel"
      :data-online="store.backendStatus?.online ? 'true' : 'false'"
      :aria-label="t(store.displayLanguage, 'backendStatus')"
    >
      <div>
        <span class="backend-status-kicker">{{ t(store.displayLanguage, "backendGateway") }}</span>
        <strong>{{ backendStatusLabel }}</strong>
      </div>
      <dl>
        <div>
          <dt>{{ t(store.displayLanguage, "backendGatewayEndpoint") }}</dt>
          <dd>{{ gatewayEndpoint }}</dd>
        </div>
        <div>
          <dt>{{ t(store.displayLanguage, "backendLastChecked") }}</dt>
          <dd>{{ lastCheckedLabel }}</dd>
        </div>
      </dl>
      <p v-if="store.backendStatus?.error" class="backend-status-error">{{ store.backendStatus.error }}</p>
    </section>
    <div class="memory-debug-panel memory-debug-panel-page" :aria-label="t(store.displayLanguage, 'memoryDebugPanel')">
      <MemoryDebugContent
        :snapshot="store.memoryDebugSnapshot"
        :display-language="store.displayLanguage"
        :loading="store.memoryDebugLoading"
        :error="store.memoryDebugError"
        @forget="forgetMemory"
        @retry="retryMemoryJob"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useAuraBotStore } from "../../../app/store";
import { t } from "../../../i18n";
import MemoryDebugContent from "../MemoryDebugContent.vue";
import SettingsPageLead from "../SettingsPageLead.vue";

const store = useAuraBotStore();
const backendStatusLabel = computed(() => {
  if (!store.backendStatus) {
    return t(store.displayLanguage, "backendStatusUnknown");
  }

  return store.backendStatus.online
    ? t(store.displayLanguage, "backendStatusOnline")
    : t(store.displayLanguage, "backendStatusOffline");
});
const gatewayEndpoint = "http://127.0.0.1:6401/health";
const lastCheckedLabel = computed(() => {
  if (!store.backendStatus?.checkedAt) {
    return t(store.displayLanguage, "notSet");
  }

  return new Date(store.backendStatus.checkedAt).toLocaleTimeString(
    store.displayLanguage === "zh" ? "zh-CN" : store.displayLanguage === "ja" ? "ja-JP" : "en",
    {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    }
  );
});

async function refreshDebugState() {
  await Promise.allSettled([
    store.loadBackendStatus(),
    store.loadMemoryDebugSnapshot()
  ]);
}

function forgetMemory(memoryId: string) {
  void store.forgetMemory(memoryId).catch(() => undefined);
}

function retryMemoryJob(jobId: string) {
  void store.retryMemoryJob(jobId).catch(() => undefined);
}

onMounted(() => {
  void refreshDebugState();
});
</script>
