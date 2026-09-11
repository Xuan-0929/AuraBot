<!--
  聊天消息列表：每条通用消息渲染为一个背景框，框内保留现有段落，更新后滚动到底部。
  不显示角色名称；共享样式以左侧助手、右侧用户区分来源，两者使用相同背景色。
  附件仅作为普通聊天元数据展示名称；文件执行产物的打开/定位不再由本组件
  或 preload 暴露，因为旧 Task artifact 协议已移除。
-->
<template>
  <div v-if="messages.length === 0" class="empty-state">
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
      :key="item.message.id"
      :class="['message', `message-${item.message.role}`]"
    >
      <p v-for="(segment, index) in item.segments" :key="index">{{ segment }}</p>
      <div v-if="item.attachments.length > 0" class="message-attachments">
        <div v-for="attachment in item.attachments" :key="attachment.id" class="message-attachment">
          <span class="message-attachment-name">{{ attachment.name }}</span>
        </div>
      </div>
    </article>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import type { Message } from "@shared/schemas/message";
import type { PetSettings } from "@shared/schemas/petSettings";
import { t } from "../../i18n";
import { getMessageDisplaySegments } from "./messageDisplay";

const props = withDefaults(
  defineProps<{
    messages: Message[];
    displayLanguage?: PetSettings["displayLanguage"];
  }>(),
  { displayLanguage: "zh" }
);

const listRef = ref<HTMLDivElement | null>(null);

const displayItems = computed(() =>
  props.messages.map((message) => ({
    message,
    segments: getMessageDisplaySegments(message),
    attachments: message.attachments ?? []
  }))
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
