<template>
  <Icon
    class="settings-nav-icon"
    :icon="iconName"
    aria-hidden="true"
  />
</template>

<script setup lang="ts">
import { Icon, addCollection } from "@iconify/vue";
import solarIcons from "@iconify-json/solar/icons.json";
import { computed } from "vue";

addCollection(solarIcons);

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

const iconNames: Record<SettingsNavIconName, string> = {
  general: "solar:tuning-square-bold-duotone",
  pet: "solar:ghost-bold-duotone",
  model: "solar:people-nearby-bold-duotone",
  task: "solar:layers-bold-duotone",
  persona: "solar:user-speak-rounded-bold-duotone",
  live2d: "solar:emoji-funny-square-bold-duotone",
  notifications: "solar:bell-bold-duotone",
  memory: "solar:book-bookmark-bold-duotone",
  settings: "solar:settings-bold-duotone"
};

const iconName = computed(() => iconNames[props.name]);
</script>
