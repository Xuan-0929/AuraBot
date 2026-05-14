<template>
  <section class="settings-page-section" :aria-label="t(store.displayLanguage, 'taskCliSettings')">
    <SettingsPageLead :title="t(store.displayLanguage, 'taskCli')" :hint="t(store.displayLanguage, 'taskCliHint')" />
    <p class="settings-page-boundary-note">{{ t(store.displayLanguage, "taskCliTimeoutHint") }}</p>
    <div class="settings-task-cli-list">
      <article v-for="runner in taskRunners" :key="runner.id" class="settings-task-cli-card">
        <label class="settings-page-toggle">
          <input
            type="checkbox"
            :checked="runner.enabled"
            :aria-label="`${t(store.displayLanguage, 'enable')} ${runner.label} ${t(store.displayLanguage, 'taskRunner')}`"
            @change="updateTaskRunner(runner.id, { enabled: ($event.currentTarget as HTMLInputElement).checked })"
          />
          <span>{{ runner.label }}</span>
        </label>
        <label class="settings-page-field settings-task-cli-priority">
          <span>{{ t(store.displayLanguage, "priority") }}</span>
          <input
            type="number"
            min="1"
            max="99"
            :value="runner.priority"
            :aria-label="`${runner.label} ${t(store.displayLanguage, 'taskRunnerPriority')}`"
            @input="updateTaskRunner(runner.id, { priority: Math.max(1, Number.parseInt(($event.currentTarget as HTMLInputElement).value, 10) || runner.priority) })"
          />
        </label>
        <label class="settings-page-field settings-task-cli-timeout">
          <span>{{ t(store.displayLanguage, "taskTimeLimit") }}</span>
          <input
            type="number"
            min="1"
            max="240"
            :value="toTimeoutMinutes(runner.timeoutMs)"
            :aria-label="`${runner.label} ${t(store.displayLanguage, 'taskTimeLimitMinutesAria')}`"
            @input="updateTaskRunner(runner.id, { timeoutMs: Math.max(1, Number.parseInt(($event.currentTarget as HTMLInputElement).value, 10) || toTimeoutMinutes(runner.timeoutMs)) * 60000 })"
          />
        </label>
        <p>
          {{ t(store.displayLanguage, "taskCliCommand") }}: {{ runner.command ?? t(store.displayLanguage, "notSet") }} ·
          {{ t(store.displayLanguage, "taskCliWorkingDirectory") }}: {{ runner.workingDirectory ?? t(store.displayLanguage, "notSet") }}
        </p>
      </article>
      <p v-if="taskRunners.length === 0" class="settings-empty">{{ t(store.displayLanguage, "configNotLoaded") }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { PublicTaskCliRunnerConfig } from "../../../../../shared/contracts/settings";
import { useAuraBotStore } from "../../../app/store";
import { t } from "../../../i18n";
import { DEFAULT_TASK_TIMEOUT_MS, toTimeoutMinutes } from "../settingsHelpers";
import SettingsPageLead from "../SettingsPageLead.vue";

const store = useAuraBotStore();
const taskRunners = computed(() => store.appConfig?.taskRunner.runners ?? []);

function updateTaskRunner(
  runnerId: PublicTaskCliRunnerConfig["id"],
  patch: Partial<Pick<PublicTaskCliRunnerConfig, "enabled" | "priority" | "timeoutMs">>
) {
  void store.updateTaskRunnerSettings(
    taskRunners.value.map((runner) => ({
      id: runner.id,
      enabled: runner.id === runnerId ? patch.enabled ?? runner.enabled : runner.enabled,
      priority: runner.id === runnerId ? patch.priority ?? runner.priority : runner.priority,
      timeoutMs:
        runner.id === runnerId
          ? patch.timeoutMs ?? runner.timeoutMs ?? DEFAULT_TASK_TIMEOUT_MS
          : runner.timeoutMs ?? DEFAULT_TASK_TIMEOUT_MS
    }))
  );
}
</script>
