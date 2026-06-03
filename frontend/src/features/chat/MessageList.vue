<template>
  <div v-if="messages.length === 0" class="empty-state">
    <span class="empty-state-kicker">{{ t(displayLanguage, "mainConversation") }}</span>
    <p>{{ t(displayLanguage, "readyWhenYouAre") }}</p>
  </div>
  <div
    v-else
    ref="listRef"
    class="message-list"
    :aria-label="t(displayLanguage, 'conversationMessages')"
    aria-live="polite"
    tabindex="0"
  >
    <article
      v-for="item in displayItems"
      :key="item.key"
      :class="['message', `message-${item.message.role}`]"
    >
      <span v-if="item.isFirstSegment" class="message-role">
        {{ item.message.role === "user" ? t(displayLanguage, "you") : t(displayLanguage, "aura") }}
      </span>
      <p>{{ item.segment }}</p>
      <div v-if="item.attachments.length > 0" class="message-attachments">
        <div v-for="attachment in item.attachments" :key="attachment.id" class="message-attachment">
          <span class="message-attachment-name">{{ attachment.name }}</span>
          <span class="message-attachment-actions">
            <button
              v-if="attachment.taskId && attachment.path"
              type="button"
              @click="void window.aurabot.openTaskResultFile({ taskId: attachment.taskId, path: attachment.path })"
            >
              {{ t(displayLanguage, "openFile") }}
            </button>
            <button
              v-if="attachment.taskId && attachment.path"
              type="button"
              @click="void window.aurabot.showTaskResultFileInFolder({ taskId: attachment.taskId, path: attachment.path })"
            >
              {{ t(displayLanguage, "showInFolder") }}
            </button>
          </span>
        </div>
      </div>
    </article>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import type { Message, MessageAttachment } from "@shared/schemas/message";
import type { PetSettings } from "@shared/schemas/petSettings";
import { t } from "../../i18n";
import { getMessageDisplaySegments } from "./messageDisplay";

const props = withDefaults(
  defineProps<{
    messages: Message[];
    displayLanguage?: PetSettings["displayLanguage"];
  }>(),
  { displayLanguage: "en" }
);

const listRef = ref<HTMLDivElement | null>(null);

const displayItems = computed(() =>
  props.messages.flatMap((message) => {
    const segments = getMessageDisplaySegments(message);
    return segments.map((segment, index) => ({
      key: `${message.id}-${index}`,
      message,
      segment,
      isFirstSegment: index === 0,
      attachments: index === segments.length - 1 ? message.attachments ?? [] : ([] as MessageAttachment[])
    }));
  })
);

watch(
  () => props.messages,
  async () => {
    await nextTick();
    const listElement = listRef.value;
    if (listElement) {
      listElement.scrollTop = listElement.scrollHeight;
    }
  },
  { deep: true, immediate: true }
);
</script>
