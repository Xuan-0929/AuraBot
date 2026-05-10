<template>
  <form
    class="pet-hover-chat"
    :aria-label="t(displayLanguage, 'petQuickChat')"
    :data-side="side"
    :data-expanded="expanded ? 'true' : 'false'"
    @submit.prevent="submit"
    @pointerenter="emit('hoveringChange', true)"
    @pointerleave="emit('hoveringChange', false)"
  >
    <template v-if="expanded">
      <textarea
        ref="textareaRef"
        v-model="content"
        :placeholder="t(displayLanguage, 'petChatPlaceholder')"
        rows="2"
        @focus="emit('focusChange', true)"
        @blur="emit('focusChange', false)"
        @keydown.enter.exact.prevent="submit"
      />
      <button class="pet-hover-chat-send" type="submit" :disabled="submitting || !content.trim()">
        {{ t(displayLanguage, "send") }}
      </button>
    </template>
    <button
      v-else
      class="pet-hover-chat-trigger"
      type="button"
      :aria-label="t(displayLanguage, 'openPetQuickChat')"
      @click="expanded = true"
    >
      {{ t(displayLanguage, "petChatCompact") }}
    </button>
  </form>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import { t, type DisplayLanguage } from "../../../i18n";

const props = defineProps<{
  side: "left" | "right";
  displayLanguage: DisplayLanguage;
}>();

const emit = defineEmits<{
  send: [string];
  hoveringChange: [boolean];
  focusChange: [boolean];
}>();

const expanded = ref(false);
const submitting = ref(false);
const content = ref("");
const textareaRef = ref<HTMLTextAreaElement | null>(null);

watch(expanded, async (isExpanded) => {
  if (!isExpanded) {
    return;
  }
  await nextTick();
  textareaRef.value?.focus();
});

async function submit() {
  const trimmed = content.value.trim();
  if (!trimmed || submitting.value) {
    return;
  }
  submitting.value = true;
  try {
    emit("send", trimmed);
    content.value = "";
    expanded.value = false;
  } finally {
    submitting.value = false;
  }
}
</script>
