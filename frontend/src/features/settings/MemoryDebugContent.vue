<!--
  展示 Python Memory owner 的 active items 与公开 job 摘要，并发出忘记/重试意图。

  组件不读取旧 memory_nodes/edges/summaries，也不接触提取 payload、lease、向量或
  SQLite 内部字段；所有 mutation 由页面经 Store/IPC 委托给 Backend。
-->
<template>
  <p v-if="error" class="memory-debug-empty backend-status-error" role="alert">{{ error }}</p>
  <p v-else-if="loading" class="memory-debug-empty" aria-live="polite">
    {{ t(displayLanguage, "memoryLoading") }}
  </p>
  <template v-else-if="snapshot">
    <div class="memory-debug-metrics" :aria-label="t(displayLanguage, 'memoryDebugMetrics')">
      <span>{{ t(displayLanguage, "activeMemories") }} {{ snapshot.items.length }}</span>
      <span>{{ t(displayLanguage, "memoryJobs") }} {{ snapshot.jobs.length }}</span>
    </div>
    <div class="memory-debug-grid memory-debug-grid-owner">
      <section class="memory-debug-column" :aria-label="t(displayLanguage, 'activeMemories')">
        <h2>{{ t(displayLanguage, "activeMemories") }}</h2>
        <div v-if="snapshot.items.length" class="memory-debug-list">
          <article v-for="memory in snapshot.items" :key="memory.memoryId" class="memory-debug-item">
            <div class="memory-debug-item-heading">
              <span class="memory-debug-kicker">{{ memory.type }} / {{ shortId(memory.memoryId) }}</span>
              <button
                type="button"
                class="memory-debug-action"
                data-memory-action="forget"
                :disabled="loading"
                :aria-label="`${t(displayLanguage, 'forgetMemory')}: ${memory.content}`"
                @click="emit('forget', memory.memoryId)"
              >
                {{ t(displayLanguage, "forget") }}
              </button>
            </div>
            <p>{{ memory.content }}</p>
            <time :datetime="memory.updatedAt">{{ formatTime(memory.updatedAt) }}</time>
          </article>
        </div>
        <p v-else class="memory-debug-empty">{{ t(displayLanguage, "noActiveMemories") }}</p>
      </section>
      <section class="memory-debug-column" :aria-label="t(displayLanguage, 'memoryJobs')">
        <h2>{{ t(displayLanguage, "memoryJobs") }}</h2>
        <div v-if="snapshot.jobs.length" class="memory-debug-list">
          <article v-for="job in snapshot.jobs" :key="job.jobId" class="memory-debug-item">
            <div class="memory-debug-item-heading">
              <span class="memory-debug-kicker">{{ job.kind }} / {{ job.state }}</span>
              <button
                v-if="job.state === 'failed'"
                type="button"
                class="memory-debug-action"
                data-memory-action="retry"
                :disabled="loading"
                :aria-label="`${t(displayLanguage, 'retryMemoryJob')}: ${job.jobId}`"
                @click="emit('retry', job.jobId)"
              >
                {{ t(displayLanguage, "retry") }}
              </button>
            </div>
            <p>{{ t(displayLanguage, "attemptCount") }} {{ job.attemptCount }}</p>
            <p v-if="job.lastError" class="memory-debug-job-error">{{ job.lastError }}</p>
            <time v-if="job.updatedAt" :datetime="job.updatedAt">{{ formatTime(job.updatedAt) }}</time>
          </article>
        </div>
        <p v-else class="memory-debug-empty">{{ t(displayLanguage, "noMemoryJobs") }}</p>
      </section>
    </div>
  </template>
  <p v-else class="memory-debug-empty">{{ t(displayLanguage, "noMemorySnapshot") }}</p>
</template>

<script setup lang="ts">
import type { BackendMemorySnapshot } from "@shared/contracts/backend";
import type { PetSettings } from "@shared/schemas/petSettings";
import { t } from "../../i18n";

withDefaults(
  defineProps<{
    snapshot?: BackendMemorySnapshot;
    displayLanguage?: PetSettings["displayLanguage"];
    loading?: boolean;
    error?: string;
  }>(),
  { displayLanguage: "en", loading: false }
);

const emit = defineEmits<{
  forget: [memoryId: string];
  retry: [jobId: string];
}>();

function shortId(value: string): string {
  return value.replace(/^memory[_-]/, "").slice(0, 10);
}

function formatTime(value: string): string {
  return new Date(value).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  });
}
</script>
