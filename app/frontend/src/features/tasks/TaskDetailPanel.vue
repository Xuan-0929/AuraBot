<template>
  <section class="task-detail-panel" :aria-label="t(displayLanguage, 'taskDetail')">
    <header class="task-detail-header">
      <div>
        <span>{{ t(displayLanguage, "taskDetail") }}</span>
        <strong>{{ summarizeTaskTitle(detail.task.title) }}</strong>
      </div>
      <button
        type="button"
        class="task-detail-close"
        :aria-label="t(displayLanguage, 'closeTaskDetail')"
        @click="onClose"
      >
        {{ t(displayLanguage, "close") }}
      </button>
    </header>

    <div class="task-detail-grid">
      <dl>
        <div>
          <dt>{{ t(displayLanguage, "taskStatus") }}</dt>
          <dd>{{ taskStatusLabel(displayLanguage, detail.task.status) }}</dd>
        </div>
        <div>
          <dt>{{ t(displayLanguage, "taskExecutor") }}</dt>
          <dd>{{ detail.executor?.runnerId ?? t(displayLanguage, "notSet") }}</dd>
        </div>
        <div>
          <dt>{{ t(displayLanguage, "taskWorkingDirectory") }}</dt>
          <dd>{{ detail.executor?.cwd ?? "—" }}</dd>
        </div>
        <div>
          <dt>{{ t(displayLanguage, "taskSandbox") }}</dt>
          <dd>{{ detail.executor?.sandbox ?? "—" }}</dd>
        </div>
        <div>
          <dt>{{ t(displayLanguage, "taskDuration") }}</dt>
          <dd>{{ formatTaskDuration(detail.durationMs) }}</dd>
        </div>
      </dl>

      <section v-if="detail.recentError" class="task-detail-error">
        <strong>{{ t(displayLanguage, "taskRecentError") }}</strong>
        <p>{{ detail.recentError }}</p>
      </section>
    </div>

    <section class="task-detail-section">
      <h2>{{ t(displayLanguage, "taskInputArtifacts") }}</h2>
      <ul v-if="detail.inputArtifacts.length > 0" class="task-detail-artifacts">
        <li v-for="artifact in detail.inputArtifacts" :key="artifact.id">
          <span>{{ artifact.name }}</span>
          <small>{{ artifact.artifactType }} · {{ formatBytes(artifact.sizeBytes) }}</small>
        </li>
      </ul>
      <p v-else class="task-detail-empty">{{ t(displayLanguage, "taskNoInputArtifacts") }}</p>
    </section>

    <section class="task-detail-section">
      <h2>{{ t(displayLanguage, "taskArtifacts") }}</h2>
      <ul v-if="detail.artifacts.length > 0" class="task-detail-artifacts">
        <li v-for="artifact in detail.artifacts" :key="artifact.id">
          <span>{{ artifact.name }}</span>
          <small>{{ artifact.artifactType }}</small>
        </li>
      </ul>
      <p v-else class="task-detail-empty">{{ t(displayLanguage, "taskNoArtifacts") }}</p>
    </section>

    <section class="task-detail-section">
      <h2>{{ t(displayLanguage, "taskTimeline") }}</h2>
      <ol class="task-detail-timeline">
        <li v-for="event in detail.timeline" :key="event.id">
          <time :datetime="event.createdAt">{{ formatTaskDetailTime(event.createdAt) }}</time>
          <div>
            <strong>{{ event.summary }}</strong>
            <span v-if="event.durationMs">{{ formatTaskDuration(event.durationMs) }}</span>
          </div>
          <ul v-if="formatPayloadSummary(event.payloadSummary).length > 0" class="task-detail-payload">
            <li v-for="item in formatPayloadSummary(event.payloadSummary)" :key="item">{{ item }}</li>
          </ul>
        </li>
      </ol>
    </section>
  </section>
</template>

<script setup lang="ts">
import type { TaskDetail } from "@aurabot/shared/contracts/taskDetail";
import type { PetSettings } from "@aurabot/shared/schemas/petSettings";
import { t, taskStatusLabel } from "../../i18n";
import { formatTaskDuration, summarizeTaskTitle } from "./taskStickyNotes";

defineProps<{
  detail: TaskDetail;
  displayLanguage: PetSettings["displayLanguage"];
  onClose: () => void;
}>();

function formatTaskDetailTime(value: string): string {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  });
}

function formatPayloadSummary(payload: Record<string, string | number | boolean>): string[] {
  return Object.entries(payload)
    .filter(([key]) => key !== "stdout" && key !== "stderr")
    .map(([key, value]) => `${key}: ${value}`);
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) {
    return "0 B";
  }

  if (value < 1024) {
    return `${value} B`;
  }

  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }

  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
</script>
