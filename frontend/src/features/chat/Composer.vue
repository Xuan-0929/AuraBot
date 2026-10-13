<!--
  主聊天纯文本输入组件：处理 Enter 发送、Shift+Enter 换行和输入区自适应高度。
  它只把正文交给 Renderer store；文件选择、拖放和路径解析在新 Task/Artifact
  协议确定前不再暴露。Agent 状态映射到发送按钮颜色和本地化提示，
  共享样式负责外观，提交、输入法与失败恢复流程保持独立。外部禁用态用于 Realtime
  语音占用输入期间阻止按钮和键盘提交，并保留已有草稿。
-->
<template>
  <form class="composer" @submit.prevent="handleSubmit">
    <div class="composer-writing-surface">
      <textarea
        ref="textareaRef"
        v-model="content"
        :disabled="disabled"
        :placeholder="t(displayLanguage, 'saySomething')"
        :aria-label="t(displayLanguage, 'saySomething')"
        rows="1"
        maxlength="8000"
        @input="resizeTextarea"
        @keydown="handleKeyDown"
      />
    </div>
    <div class="composer-action-rail">
        <button
          type="submit"
          class="composer-send-button"
          :data-status="agentStatus"
          :aria-description="agentStatusLabel(displayLanguage, agentStatus)"
          :disabled="disabled || submitting || !content.trim()"
          :aria-label="t(displayLanguage, 'send')"
          :title="`${t(displayLanguage, 'send')} · ${agentStatusLabel(displayLanguage, agentStatus)}`"
        >
          <Icon class="composer-action-icon" icon="solar:arrow-up-linear" aria-hidden="true" />
        </button>
    </div>
    <p v-if="disabled && disabledReason" role="status">{{ disabledReason }}</p>
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
    disabled?: boolean;
    disabledReason?: string;
    onSend(content: string): Promise<void>;
  }>(),
  { displayLanguage: "zh", agentStatus: "idle", disabled:false, disabledReason:'' }
);

const content = ref("");
const submitting = ref(false);
const textareaRef = ref<HTMLTextAreaElement | null>(null);
const MAX_COMPOSER_HEIGHT = 198;

async function sendContent(nextContent: string) {
  if (props.disabled || !nextContent.trim() || submitting.value) {
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
  void sendContent(content.value).catch(() => undefined);
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
  void sendContent(textarea.value).catch(() => undefined);
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
