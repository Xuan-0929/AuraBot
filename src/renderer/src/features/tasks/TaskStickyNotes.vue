<template>
  <aside
    v-if="visibleTasks.length > 0"
    :class="[
      'task-sticky-notes',
      `task-sticky-notes-${variant}`,
      variant === 'pet' ? 'desktop-pet-sticky-notes' : undefined
    ]"
    :aria-label="ariaLabel === false ? undefined : ariaLabel ?? t(displayLanguage, 'taskStickyNotes')"
  >
    <header v-if="showHeader" class="task-sticky-notes-header">
      <strong>{{ t(displayLanguage, "taskStickyNotes") }}</strong>
      <span v-if="variant === 'pet'">{{ t(displayLanguage, "taskStickyNoteHint") }}</span>
    </header>
    <article v-for="task in visibleTasks" :key="task.id" class="task-sticky-note">
      <div class="task-sticky-note-copy">
        <strong>{{ summarizeTaskTitle(task.title) }}</strong>
        <span>{{ summarizeTaskMeta(task, displayLanguage) }}</span>
      </div>
      <div v-if="variant === 'console' && isStickyTaskActive(task.status) && onCancelTask" class="task-sticky-note-actions">
        <button
          class="task-sticky-note-action task-sticky-note-action-cancel"
          type="button"
          :aria-label="`${t(displayLanguage, 'cancelTask')}: ${summarizeTaskTitle(task.title)}`"
          @click="onCancelTask(task.id)"
        >
          {{ t(displayLanguage, "cancel") }}
        </button>
      </div>
      <div v-else-if="variant === 'console' && isStickyTaskSettled(task.status) && onDeleteTask" class="task-sticky-note-actions">
        <button
          class="task-sticky-note-action task-sticky-note-action-delete"
          type="button"
          :aria-label="`${t(displayLanguage, 'deleteTask')}: ${summarizeTaskTitle(task.title)}`"
          @click="onDeleteTask(task.id)"
        >
          {{ t(displayLanguage, "delete") }}
        </button>
      </div>
    </article>
  </aside>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { PetSettings } from "../../../../shared/schemas/petSettings";
import type { Task } from "../../../../shared/schemas/task";
import { t } from "../../i18n";
import {
  isStickyTaskActive,
  isStickyTaskSettled,
  pickVisiblePetTasks,
  summarizeTaskMeta,
  summarizeTaskTitle
} from "./taskStickyNotes";

const props = withDefaults(
  defineProps<{
    tasks: Task[];
    displayLanguage: PetSettings["displayLanguage"];
    variant: "pet" | "console";
    showHeader?: boolean;
    ariaLabel?: string | false;
    onCancelTask?: (taskId: string) => void;
    onDeleteTask?: (taskId: string) => void;
  }>(),
  { showHeader: true, ariaLabel: undefined }
);

const visibleTasks = computed(() => (props.variant === "pet" ? pickVisiblePetTasks(props.tasks) : props.tasks));
</script>
