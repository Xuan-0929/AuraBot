<!-- 密钥输入控件：显示 Main 提供的前后四位与等长黑点预览，聚焦选中后支持替换或删除。
     已保存密钥的中间原文留在 Main；向 SettingsWindow 只提交用户新输入的值，遮罩不进入配置。
     父页面通过 cleared 区分保留和清除，并复用原有保存 IPC。 -->
<template>
  <input
    :type="masked ? 'text' : 'password'"
    data-secret-input
    spellcheck="false"
    autocomplete="new-password"
    :value="masked ? preview : modelValue"
    :title="masked ? '已保存；输入新密钥替换，删除后保存清除' : '输入密钥，留空后保存清除'"
    @focus="($event.target as HTMLInputElement).select()"
    @beforeinput="prepareEdit"
    @input="update"
  />
</template>
<script setup lang="ts">
import { computed } from 'vue';
const props = defineProps<{ modelValue: string; preview?: string; cleared?: boolean }>();
const emit = defineEmits<{ 'update:modelValue': [value: string] }>();
const masked = computed(() => props.preview && !props.cleared && !props.modelValue);
function prepareEdit(event: Event) {
  if (!masked.value) return;
  const inputEvent = event as InputEvent;
  if (inputEvent.inputType.startsWith('delete')) {
    event.preventDefault();
    emit('update:modelValue', '');
  } else {
    // Editing a preview replaces the whole secret; its visible prefix/suffix are not editable data.
    (event.target as HTMLInputElement).select();
  }
}
function update(event: Event) {
  const input = event.target as HTMLInputElement;
  // A partial deletion of the display mask clears it; remaining mask characters are never a key.
  const value = masked.value && input.value.includes('•') ? '' : input.value;
  input.value = value;
  emit('update:modelValue', value);
}
</script>
