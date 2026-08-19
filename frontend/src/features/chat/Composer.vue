<!--
  主聊天纯文本输入组件：处理 Enter 发送、Shift+Enter 换行和输入区自适应高度。
  它只把正文交给 Renderer store；文件选择、拖放和路径解析在新 Task/Artifact
  协议确定前不再暴露，Agent 状态仅用于当前聊天生命周期展示。
-->
<template>
  <form class="composer" @submit.prevent="handleSubmit">
    <div class="composer-writing-surface">
      <textarea
        ref="textareaRef"
        v-model="content"
        :placeholder="t(displayLanguage, 'saySomething')"
        :aria-label="t(displayLanguage, 'saySomething')"
        rows="1"
        @input="resizeTextarea"
        @keydown="handleKeyDown"
      />
    </div>
    <div class="composer-action-rail">
      <span class="composer-presence" aria-live="polite">
        <span :class="['composer-status-dot', `composer-status-dot-${agentStatus}`]" aria-hidden="true"></span>
        {{ agentStatusLabel(displayLanguage, agentStatus) }}
      </span>
      <div class="composer-rail-actions composer-rail-actions-end">
        <button
          v-if="content.trim()"
          type="submit"
          class="composer-send-button"
          :disabled="submitting"
          :aria-label="t(displayLanguage, 'send')"
          :title="t(displayLanguage, 'send')"
        >
          <Icon class="composer-action-icon" icon="solar:arrow-up-linear" aria-hidden="true" />
        </button>
      </div>
    </div>
  </form>
</template>

<script setup lang="ts">
import { nextTick, ref } from "vue";
import { Icon, addCollection } from "@iconify/vue";
import solarIcons from "@iconify-json/solar/icons.json";
import type { AgentStatus } from "@shared/contracts/chat";
import type { PetSettings } from "@shared/schemas/petSettings";
import { agentStatusLabel, t } from "../../i18n";

addCollection(solarIcons);

const props = withDefaults(
  defineProps<{
    displayLanguage?: PetSettings["displayLanguage"];
    agentStatus?: AgentStatus;
    onSend(content: string): Promise<void>;
  }>(),
  { displayLanguage: "en", agentStatus: "idle" }
);

const content = ref("");
const submitting = ref(false);
const textareaRef = ref<HTMLTextAreaElement | null>(null);
const MAX_COMPOSER_HEIGHT = 198;

async function sendContent(nextContent: string) {
  if (!nextContent.trim() || submitting.value) {
    return;
  }

  submitting.value = true;
  content.value = "";
  try {
    await props.onSend(nextContent);
  } catch (error) {
    content.value ||= nextContent;
    throw error;
  } finally {
    submitting.value = false;
    await nextTick();
    resizeTextarea();
  }
}

function handleSubmit() {
  void sendContent(content.value);
}

function handleKeyDown(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) {
    return;
  }

  const textarea = event.currentTarget as HTMLTextAreaElement;

  if (event.shiftKey) {
    event.preventDefault();
    insertLineBreak(textarea);
    return;
  }

  event.preventDefault();
  void sendContent(textarea.value);
}

function resizeTextarea() {
  const textarea = textareaRef.value;
  if (!textarea) {
    return;
  }

  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_COMPOSER_HEIGHT)}px`;
}

function insertLineBreak(textarea: HTMLTextAreaElement) {
  const selectionStart = textarea.selectionStart ?? textarea.value.length;
  const selectionEnd = textarea.selectionEnd ?? textarea.value.length;

  textarea.setRangeText("\n", selectionStart, selectionEnd, "end");
  content.value = textarea.value;
  resizeTextarea();
}

</script>
