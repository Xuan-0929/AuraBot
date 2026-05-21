<template>
  <aside class="task-sticky-notes task-sticky-notes-pet desktop-pet-sticky-notes" :aria-label="t(displayLanguage, 'taskStickyNotes')">
    <header class="task-sticky-notes-header">
      <strong>{{ t(displayLanguage, "taskStickyNotes") }}</strong>
      <span>{{ t(displayLanguage, "taskStickyNoteHint") }}</span>
    </header>
    <article v-for="task in visibleTasks" :key="task.id" class="task-sticky-note">
      <div class="task-sticky-note-copy">
        <strong>{{ summarizeTitle(task.title) }}</strong>
        <span>{{ taskProgressLabel(displayLanguage, task) }}</span>
      </div>
    </article>
  </aside>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { Task } from "@aurabot/shared/schemas/task";
import { t, taskProgressLabel, type DisplayLanguage } from "../../../i18n";

const props = defineProps<{
  tasks: Task[];
  displayLanguage: DisplayLanguage;
}>();

const visibleTasks = computed(() => props.tasks.filter((task) => isStickyTaskActive(task.status)).slice(0, 2));

function summarizeTitle(title: string): string {
  const cleaned = title
    .trim()
    .replace(/[。.!！]*$/u, "")
    .replace(/^(请|麻烦)\s*/u, "")
    .replace(
      /^(帮我把|帮我做一下|帮我写一下|帮我整理一下|帮我生成一下|帮我做|帮我写|帮我整理|帮我生成|帮我|做一下|写一下|整理一下|生成一下|做|写|整理|生成)\s*/u,
      ""
    )
    .replace(/^(一份|一个|一篇|一版|这份|这个|一下)\s*/u, "")
    .trim();
  return cleaned || title;
}

function isStickyTaskActive(status: Task["status"]): boolean {
  return status === "queued" || status === "running" || status === "waiting";
}
</script>
