<!-- Projects Backend activity through shared i18n; acoustic silence uses the existing idle indicator. -->
<template>
  <div class="status-bar" aria-label="Agent status">
    <span :class="['status-dot', `status-dot-${status === 'no_audio' ? 'idle' : status}`]" />
    <span>{{ agentStatusLabel(displayLanguage, status) }}</span>
  </div>
</template>

<script setup lang="ts">
import type { AgentStatus } from "@shared/contracts/chat";
import type { PetSettings } from "@shared/schemas/petSettings";
import { agentStatusLabel } from "../../i18n";

withDefaults(
  defineProps<{
    status: AgentStatus;
    displayLanguage?: PetSettings["displayLanguage"];
  }>(),
  { displayLanguage: "zh" }
);
</script>
