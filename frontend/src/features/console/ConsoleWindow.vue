<!--
  Console 主窗口：呈现主聊天消息与输入区。
  消息由 Pinia store 从主进程加载，输入通过现有聊天 IPC 发送；该组件不拥有
  Task 状态、执行控制或侧边栏，只与通用聊天组件和玻璃面板组合。
-->
<template>
  <main class="console-window" :aria-label="t(store.displayLanguage, 'consoleTitle')">
    <button
      type="button"
      class="console-settings-button"
      :aria-label="t(store.displayLanguage, 'openAuraBotSettings')"
      :title="t(store.displayLanguage, 'settings')"
      @click="void store.openSettingsWindow()"
    >
      <Icon icon="solar:settings-linear" aria-hidden="true" />
    </button>
    <div class="console-body">
      <section class="console-chat" :aria-label="t(store.displayLanguage, 'mainConversation')">
        <MessageList :messages="store.messages" :display-language="store.displayLanguage" />
        <ConsoleGlassPanel variant="control" class="console-composer-glass">
          <Composer
            :display-language="store.displayLanguage"
            :agent-status="store.agentStatus"
            :on-send="(content, attachments) => store.sendMessage(content, 'console', attachments)"
          />
        </ConsoleGlassPanel>
      </section>
    </div>
  </main>
</template>

<script setup lang="ts">
import { Icon, addCollection } from "@iconify/vue";
import solarIcons from "@iconify-json/solar/icons.json";
import { useAuraBotStore } from "../../app/store";
import { t } from "../../i18n";
import Composer from "../chat/Composer.vue";
import MessageList from "../chat/MessageList.vue";
import ConsoleGlassPanel from "./ConsoleGlassPanel.vue";

addCollection(solarIcons);
const store = useAuraBotStore();
</script>
