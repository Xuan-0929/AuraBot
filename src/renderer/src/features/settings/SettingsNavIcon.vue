<template>
  <svg
    class="settings-nav-icon"
    viewBox="0 0 24 24"
    fill="none"
    aria-hidden="true"
  >
    <path
      v-for="path in paths"
      :key="path"
      :d="path"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
</template>

<script setup lang="ts">
import { computed } from "vue";

export type SettingsNavIconName =
  | "general"
  | "pet"
  | "model"
  | "task"
  | "persona"
  | "live2d"
  | "notifications"
  | "memory"
  | "settings";

const props = defineProps<{
  name: SettingsNavIconName;
}>();

const iconPaths: Record<SettingsNavIconName, string[]> = {
  general: ["M4 7h10", "M18 7h2", "M16 5v4", "M4 12h3", "M11 12h9", "M9 10v4", "M4 17h12", "M20 17h0", "M18 15v4"],
  pet: ["M8.5 10.5c-1.3-1.8-3.5-2.8-4.2-1.8-.7 1 1.1 2.5 3 3.1", "M15.5 10.5c1.3-1.8 3.5-2.8 4.2-1.8.7 1-1.1 2.5-3 3.1", "M7 13.5c0-3 2.2-5.5 5-5.5s5 2.5 5 5.5-2.2 5-5 5-5-2-5-5Z", "M10 13h.1", "M14 13h.1", "M10.2 16c1.1.7 2.5.7 3.6 0"],
  model: ["M8 4h8", "M8 20h8", "M4 8v8", "M20 8v8", "M7 7h10v10H7z", "M10 10h4v4h-4z"],
  task: ["M5 5h14v14H5z", "M8.5 12.5l2.2 2.2 4.8-5.4"],
  persona: ["M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z", "M4.5 20c1.2-3.7 4-5.5 7.5-5.5s6.3 1.8 7.5 5.5"],
  live2d: ["M4 5h16v11H4z", "M9 20h6", "M12 16v4", "M8.5 9.5h.1", "M15.5 9.5h.1", "M9.8 12.6c1.4.9 3 .9 4.4 0"],
  notifications: ["M6.5 16.5h11", "M8 16.5V11a4 4 0 0 1 8 0v5.5", "M10 19a2 2 0 0 0 4 0", "M12 4V3"],
  memory: ["M6 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z", "M18 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z", "M6 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z", "M18 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z", "M8 5h8", "M7.2 8.4l9.6 7.2", "M16.8 8.4l-9.6 7.2"],
  settings: ["M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z", "M19.4 13.5a7.8 7.8 0 0 0 .1-1.5 7.8 7.8 0 0 0-.1-1.5l2-1.5-2-3.4-2.4 1a8.1 8.1 0 0 0-2.6-1.5L14 2.5h-4l-.4 2.6A8.1 8.1 0 0 0 7 6.6l-2.4-1-2 3.4 2 1.5a7.8 7.8 0 0 0-.1 1.5 7.8 7.8 0 0 0 .1 1.5l-2 1.5 2 3.4 2.4-1a8.1 8.1 0 0 0 2.6 1.5l.4 2.6h4l.4-2.6a8.1 8.1 0 0 0 2.6-1.5l2.4 1 2-3.4-2-1.5Z"]
};

const paths = computed(() => iconPaths[props.name]);
</script>
