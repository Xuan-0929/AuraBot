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
    <div class="composer-input-row">
      <button
        type="button"
        class="composer-attach-button"
        :aria-label="t(displayLanguage, 'attachFile')"
        @click="fileInputRef?.click()"
      >
        <span class="composer-paperclip" aria-hidden="true"></span>
      </button>
      <textarea
        v-model="content"
        :placeholder="t(displayLanguage, 'saySomething')"
        rows="2"
        @keydown="handleKeyDown"
      />
      <button type="submit" :disabled="submitting || !content.trim()">
        {{ t(displayLanguage, "send") }}
      </button>
    </div>
  </form>
</template>

<script setup lang="ts">
import { ref } from "vue";
import type { ChatInputAttachment } from "@aurabot/shared/contracts/chat";
import type { PetSettings } from "@aurabot/shared/schemas/petSettings";
import { t } from "../../i18n";

const props = withDefaults(
  defineProps<{
    displayLanguage?: PetSettings["displayLanguage"];
    onSend(content: string, attachments: ChatInputAttachment[]): Promise<void>;
  }>(),
  { displayLanguage: "en" }
);

const content = ref("");
const submitting = ref(false);
const fileInputRef = ref<HTMLInputElement | null>(null);
const attachments = ref<DisplayChatInputAttachment[]>([]);

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
