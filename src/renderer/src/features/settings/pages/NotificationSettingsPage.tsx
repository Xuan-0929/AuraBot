import type { NotificationSettings } from "../../../../../shared/contracts/notificationSettings";
import { t } from "../../../i18n";
import type { ProactiveSettingsPatch, PublicAppConfig } from "../../../../../shared/contracts/settings";
import type { PetSettings } from "../../../../../shared/schemas/petSettings";
import { SettingsPageLead } from "../SettingsPageLead";

export function NotificationSettingsPage({
  petSettings,
  appConfig,
  notificationSettings,
  onUpdateNotificationSettings,
  onUpdateProactiveSettings
}: {
  petSettings: PetSettings;
  appConfig?: PublicAppConfig;
  notificationSettings?: NotificationSettings;
  onUpdateNotificationSettings(
    patch: Partial<Omit<NotificationSettings, "permissionState">>
  ): Promise<void>;
  onUpdateProactiveSettings(patch: ProactiveSettingsPatch): Promise<void>;
}) {
  const displayLanguage = petSettings.displayLanguage;

  return (
    <section className="settings-page-section" aria-label={t(displayLanguage, "notifications")}>
      <div className="settings-page-section-header">
        <SettingsPageLead
          title={t(displayLanguage, "notifications")}
          hint={t(displayLanguage, "notificationSettingsHint")}
        />
      </div>
      <p className="settings-page-boundary-note">{t(displayLanguage, "notificationAppRunningOnlyHint")}</p>
      <label className="settings-page-toggle">
        <input
          type="checkbox"
          checked={notificationSettings?.systemNotificationsEnabled ?? true}
          aria-label={t(displayLanguage, "enableSystemNotifications")}
          onChange={(event) =>
            void onUpdateNotificationSettings({
              systemNotificationsEnabled: event.currentTarget.checked
            })
          }
        />
        <span>{t(displayLanguage, "systemNotifications")}</span>
      </label>
      <div className="settings-page-field">
        <span>{t(displayLanguage, "notificationPermission")}</span>
        <strong>{notificationPermissionLabel(notificationSettings?.permissionState, displayLanguage)}</strong>
      </div>
      <label className="settings-page-toggle">
        <input
          type="checkbox"
          checked={notificationSettings?.followUpNotificationsEnabled ?? true}
          aria-label={t(displayLanguage, "enableFollowUpNotifications")}
          onChange={(event) =>
            void onUpdateNotificationSettings({
              followUpNotificationsEnabled: event.currentTarget.checked
            })
          }
        />
        <span>{t(displayLanguage, "followUpNotifications")}</span>
      </label>
      <label className="settings-page-toggle">
        <input
          type="checkbox"
          checked={notificationSettings?.timeReminderNotificationsEnabled ?? true}
          aria-label={t(displayLanguage, "enableTimeReminderNotifications")}
          onChange={(event) =>
            void onUpdateNotificationSettings({
              timeReminderNotificationsEnabled: event.currentTarget.checked
            })
          }
        />
        <span>{t(displayLanguage, "timeReminderNotifications")}</span>
      </label>
      <label className="settings-page-toggle">
        <input
          type="checkbox"
          checked={notificationSettings?.idleNudgeNotificationsEnabled ?? false}
          aria-label={t(displayLanguage, "enableIdleNudgeNotifications")}
          onChange={(event) =>
            void onUpdateNotificationSettings({
              idleNudgeNotificationsEnabled: event.currentTarget.checked
            })
          }
        />
        <span>{t(displayLanguage, "idleNudgeNotifications")}</span>
      </label>
      <label className="settings-page-field settings-proactive-wait">
        <span>{t(displayLanguage, "proactiveWaitMinutes")}</span>
        <input
          type="number"
          min={1}
          max={240}
          value={millisecondsToMinutes(appConfig?.proactive.idleThresholdMs ?? 600000)}
          aria-label={t(displayLanguage, "proactiveMessageWaitMinutes")}
          onInput={(event) =>
            void onUpdateProactiveSettings({
              idleThresholdMs: minutesToMilliseconds(Number.parseInt(event.currentTarget.value, 10) || 1)
            })
          }
        />
      </label>
    </section>
  );
}

function millisecondsToMinutes(value: number): number {
  return Math.max(1, Math.round(value / 60_000));
}

function minutesToMilliseconds(value: number): number {
  return Math.max(1, value) * 60_000;
}

function notificationPermissionLabel(
  value: NotificationSettings["permissionState"] | undefined,
  displayLanguage: PetSettings["displayLanguage"]
): string {
  if (value === "unsupported") {
    return t(displayLanguage, "notificationPermissionUnsupported");
  }

  return t(displayLanguage, "notificationPermissionSystemManaged");
}
