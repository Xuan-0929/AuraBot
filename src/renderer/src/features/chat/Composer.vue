<template>
  <form class="composer" @submit.prevent="handleSubmit">
    <textarea
      v-model="content"
      :placeholder="t(displayLanguage, 'saySomething')"
      rows="2"
      @keydown="handleKeyDown"
    />
    <button type="submit" :disabled="submitting || !content.trim()">
      {{ t(displayLanguage, "send") }}
    </button>
  </form>
</template>

<script setup lang="ts">
import { ref } from "vue";
import type { PetSettings } from "../../../../shared/schemas/petSettings";
import { t } from "../../i18n";

const props = withDefaults(
  defineProps<{
    displayLanguage?: PetSettings["displayLanguage"];
    onSend(content: string): Promise<void>;
  }>(),
  { displayLanguage: "en" }
);

const content = ref("");
const submitting = ref(false);

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
  }
}

function handleSubmit() {
  void sendContent(content.value);
}

function handleKeyDown(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.isComposing) {
    return;
  }

  const textarea = event.currentTarget as HTMLTextAreaElement;

  if (event.ctrlKey) {
    event.preventDefault();
    insertLineBreak(textarea);
    return;
  }

  event.preventDefault();
  void sendContent(textarea.value);
}

function insertLineBreak(textarea: HTMLTextAreaElement) {
  const selectionStart = textarea.selectionStart ?? textarea.value.length;
  const selectionEnd = textarea.selectionEnd ?? textarea.value.length;

  textarea.setRangeText("\n", selectionStart, selectionEnd, "end");
  content.value = textarea.value;
}
</script>
