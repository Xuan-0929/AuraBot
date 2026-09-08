<!-- Maps the six settings destinations to bundled linear SVG icons. This presentation-only
     component retains the Solar collection viewport when passing local icon data to Iconify,
     keeping complete SVG strokes and navigation labels in SettingsWindow accessible
     without network icon requests or duplicate spoken icon names. -->
<template><Icon class="settings-nav-icon" :icon="{ width: solarIcons.width, height: solarIcons.height, ...icons[name] }" aria-hidden="true" /></template>
<script setup lang="ts">
import { Icon } from '@iconify/vue';
import solarIcons from '@iconify-json/solar/icons.json';
export type SettingsNavIconName = 'model' | 'voice' | 'desktop' | 'workspace' | 'profiles' | 'runtime';
defineProps<{ name: SettingsNavIconName }>();
const icons = {
  model: solarIcons.icons['cpu-linear'],
  voice: solarIcons.icons['microphone-linear'],
  desktop: solarIcons.icons['widget-linear'],
  workspace: solarIcons.icons['folder-linear'],
  profiles: solarIcons.icons['user-rounded-linear'],
  runtime: solarIcons.icons['pulse-linear']
};
</script>
<style scoped>.settings-nav-icon{width:20px;height:20px;flex:none}</style>
