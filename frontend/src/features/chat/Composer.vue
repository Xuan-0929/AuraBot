<template>
  <form class="composer" @submit.prevent="handleSubmit" @dragover.prevent @drop.prevent="handleDrop">
    <input ref="fileInputRef" class="composer-file-input" type="file" multiple @change="handleFileInput" />
    <div v-if="attachments.length > 0" class="composer-attachments">
      <span v-for="attachment in attachments" :key="attachment.path" class="composer-attachment">
        <span class="composer-attachment-dot" aria-hidden="true"></span>
        <span class="composer-attachment-name" :title="attachment.fullName">{{ attachment.displayName }}</span>
        <button
          type="button"
          class="composer-attachment-remove"
          :aria-label="`${t(displayLanguage, 'removeAttachment')}: ${attachment.displayName}`"
          @click="removeAttachment(attachment.path)"
        >
          ×
        </button>
      </span>
    </div>
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
      <div class="composer-rail-actions">
        <button
          type="button"
          class="composer-attach-button"
          :aria-label="t(displayLanguage, 'attachFile')"
          :title="t(displayLanguage, 'attachFile')"
          @click="fileInputRef?.click()"
        >
          <span class="composer-add-icon" aria-hidden="true">+</span>
        </button>
      </div>
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
import type { AgentStatus, ChatInputAttachment } from "@shared/contracts/chat";
import type { PetSettings } from "@shared/schemas/petSettings";
import { agentStatusLabel, t } from "../../i18n";

addCollection(solarIcons);

const props = withDefaults(
  defineProps<{
    displayLanguage?: PetSettings["displayLanguage"];
    agentStatus?: AgentStatus;
    onSend(content: string, attachments: ChatInputAttachment[]): Promise<void>;
  }>(),
  { displayLanguage: "en", agentStatus: "idle" }
);

const content = ref("");
const submitting = ref(false);
const fileInputRef = ref<HTMLInputElement | null>(null);
const textareaRef = ref<HTMLTextAreaElement | null>(null);
const attachments = ref<DisplayChatInputAttachment[]>([]);
const MAX_COMPOSER_HEIGHT = 198;

type DisplayChatInputAttachment = ChatInputAttachment & {
  displayName: string;
  fullName: string;
};

async function sendContent(nextContent: string) {
  if (!nextContent.trim() || submitting.value) {
    return;
  }

  submitting.value = true;
  content.value = "";
  const nextAttachments = attachments.value;
  attachments.value = [];
  try {
    await props.onSend(nextContent, nextAttachments);
  } catch (error) {
    content.value ||= nextContent;
    attachments.value = nextAttachments;
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

function handleFileInput(event: Event) {
  const input = event.currentTarget as HTMLInputElement;
  void addFiles(input.files);
  input.value = "";
}

function handleDrop(event: DragEvent) {
  void addFiles(event.dataTransfer?.files);
}

async function addFiles(files: FileList | null | undefined) {
  if (!files) {
    return;
  }

  for (const file of Array.from(files)) {
    const path = await readElectronFilePath(file);

    if (!path || attachments.value.some((attachment) => attachment.path === path)) {
      continue;
    }

    const fullName = file.name || basenameFromPath(path) || "attachment";

    attachments.value.push({
      kind: file.type.startsWith("image/") ? "image" : "file",
      name: fullName,
      displayName: abbreviateMiddle(fullName),
      fullName,
      path,
      mimeType: file.type || undefined,
      sizeBytes: file.size
    });
  }
}

function removeAttachment(path: string) {
  attachments.value = attachments.value.filter((attachment) => attachment.path !== path);
}

function basenameFromPath(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? "";
}

function abbreviateMiddle(name: string): string {
  const normalized = name.trim();
  const maxWeight = 18;

  if (measureNameWeight(normalized) <= maxWeight) {
    return normalized;
  }

  const head = takeWeightedPrefix(normalized, 6);
  const tail = takeWeightedSuffix(normalized, maxWeight - measureNameWeight(head) - 3);

  return `${head}...${tail}`;
}

function takeWeightedPrefix(value: string, maxWeight: number): string {
  let weight = 0;
  let result = "";

  for (const char of Array.from(value)) {
    const nextWeight = weight + measureNameWeight(char);

    if (nextWeight > maxWeight) {
      break;
    }

    result += char;
    weight = nextWeight;
  }

  return result;
}

function takeWeightedSuffix(value: string, maxWeight: number): string {
  let weight = 0;
  let result = "";

  for (const char of Array.from(value).reverse()) {
    const nextWeight = weight + measureNameWeight(char);

    if (nextWeight > maxWeight) {
      break;
    }

    result = `${char}${result}`;
    weight = nextWeight;
  }

  return result;
}

function measureNameWeight(value: string): number {
  return Array.from(value).reduce((total, char) => total + (char.charCodeAt(0) > 255 ? 2 : 1), 0);
}

async function readElectronFilePath(file: File): Promise<string | undefined> {
  return window.aurabot.getPathForFile?.(file) || (file as File & { path?: string }).path;
}
</script>
