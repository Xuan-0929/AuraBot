<!--
  通知设置页：管理全局桌面通知、Memory 跟进和 idle nudge 偏好。
  修改通过 renderer store/IPC 写入主进程设置存储；定时 Task 提醒开关已移除。
-->
<template>
  <section class="settings-page-section" :aria-label="t(store.displayLanguage, 'notifications')">
    <div class="settings-page-section-header">
      <SettingsPageLead :title="t(store.displayLanguage, 'notifications')" :hint="t(store.displayLanguage, 'notificationSettingsHint')" />
    </div>
    <p class="settings-page-boundary-note">{{ t(store.displayLanguage, "notificationAppRunningOnlyHint") }}</p>
    <label class="settings-page-toggle">
      <input type="checkbox" :checked="store.notificationSettings?.systemNotificationsEnabled ?? true" :aria-label="t(store.displayLanguage, 'enableSystemNotifications')" @change="void store.updateNotificationSettings({ systemNotificationsEnabled: ($event.currentTarget as HTMLInputElement).checked })" />
      <span>{{ t(store.displayLanguage, "systemNotifications") }}</span>
    </label>
    <div class="settings-page-field">
      <span>{{ t(store.displayLanguage, "notificationPermission") }}</span>
      <strong>{{ notificationPermissionLabel(store.notificationSettings?.permissionState, store.displayLanguage) }}</strong>
    </div>
    <label class="settings-page-toggle">
      <input type="checkbox" :checked="store.notificationSettings?.followUpNotificationsEnabled ?? true" :aria-label="t(store.displayLanguage, 'enableFollowUpNotifications')" @change="void store.updateNotificationSettings({ followUpNotificationsEnabled: ($event.currentTarget as HTMLInputElement).checked })" />
      <span>{{ t(store.displayLanguage, "followUpNotifications") }}</span>
    </label>
    <label class="settings-page-toggle">
      <input type="checkbox" :checked="store.notificationSettings?.idleNudgeNotificationsEnabled ?? false" :aria-label="t(store.displayLanguage, 'enableIdleNudgeNotifications')" @change="void store.updateNotificationSettings({ idleNudgeNotificationsEnabled: ($event.currentTarget as HTMLInputElement).checked })" />
      <span>{{ t(store.displayLanguage, "idleNudgeNotifications") }}</span>
    </label>
    <label class="settings-page-field settings-proactive-wait">
      <span>{{ t(store.displayLanguage, "proactiveWaitMinutes") }}</span>
      <input
        type="number"
        min="1"
        max="240"
        :value="millisecondsToMinutes(store.appConfig?.proactive.idleThresholdMs ?? 600000)"
        :aria-label="t(store.displayLanguage, 'proactiveMessageWaitMinutes')"
        @input="void store.updateProactiveSettings({ idleThresholdMs: minutesToMilliseconds(Number.parseInt(($event.currentTarget as HTMLInputElement).value, 10) || 1) })"
      />
    </label>
  </section>
</template>

<script setup lang="ts">
import { useAuraBotStore } from "../../../app/store";
import { t } from "../../../i18n";
import { millisecondsToMinutes, minutesToMilliseconds, notificationPermissionLabel } from "../settingsHelpers";
import SettingsPageLead from "../SettingsPageLead.vue";

const store = useAuraBotStore();
</script>
