<template>
  <div class="status-bar" aria-label="Agent status">
    <span :class="['status-dot', `status-dot-${status}`]" />
    <span>{{ agentStatusLabel(displayLanguage, status) }}</span>
  </div>
</template>

<script setup lang="ts">
import type { AgentStatus } from "@aurabot/shared/contracts/chat";
import type { PetSettings } from "@aurabot/shared/schemas/petSettings";
import { agentStatusLabel } from "../../i18n";

withDefaults(
  defineProps<{
    status: AgentStatus;
    displayLanguage?: PetSettings["displayLanguage"];
  }>(),
  { displayLanguage: "en" }
);
</script>
