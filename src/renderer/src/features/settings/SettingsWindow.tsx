import { useState } from "react";
import type { MemoryDebugSnapshot } from "../../../../shared/contracts/ipc";
import type { Live2DModelState } from "../../../../shared/contracts/live2dModel";
import type { NotificationSettings } from "../../../../shared/contracts/notificationSettings";
import type {
  PublicModelHealthMap,
  ModelProviderSettingsPatch,
  ModelRoleKey,
  PublicAppConfig,
  ProactiveSettingsPatch,
  TaskRunnerSettingsPatch
} from "../../../../shared/contracts/settings";
import { createPersonaSettingsFromProfile, type PersonaSettings } from "../../../../shared/contracts/personaSettings";
import type { PetSettings, PetSettingsPatch } from "../../../../shared/schemas/petSettings";
import type { Profile } from "../../../../shared/schemas/profile";
import { t } from "../../i18n";
import { SettingsCenterShell, type SettingsNavItem, type SettingsPageKey } from "./SettingsCenterShell";
import {
  GeneralSettingsPage,
  Live2DModelSettingsPage,
  MemoryDebugSettingsPage,
  ModelSettingsPage,
  NotificationSettingsPage,
  PersonaSettingsPage,
  PetBehaviorSettingsPage
} from "./pages";

export interface SettingsWindowProps {
  profile?: Profile;
  petSettings: PetSettings;
  appConfig?: PublicAppConfig;
  modelHealth?: PublicModelHealthMap;
  live2dModelState?: Live2DModelState;
  notificationSettings?: NotificationSettings;
  memoryDebugSnapshot?: MemoryDebugSnapshot;
  onUpdatePetSettings(patch: PetSettingsPatch): Promise<void>;
  onUpdateTaskRunnerSettings(runners: TaskRunnerSettingsPatch["runners"]): Promise<void>;
  onUpdateModelProviderSettings(patch: ModelProviderSettingsPatch): Promise<void>;
  onUpdateProactiveSettings(patch: ProactiveSettingsPatch): Promise<void>;
  onUpdatePersonaSettings(settings: PersonaSettings): Promise<void>;
  onSetCurrentLive2DModel(modelId: string): Promise<void>;
  onUpdateNotificationSettings(
    patch: Partial<Omit<NotificationSettings, "permissionState">>
  ): Promise<void>;
  onLoadMemoryDebugSnapshot(): Promise<void>;
  onOpenConfigFile(): Promise<void>;
  onTestModelConnection(role: ModelRoleKey): Promise<void>;
}

export function SettingsWindow(props: SettingsWindowProps) {
  const displayLanguage = props.petSettings.displayLanguage;
  const [currentPage, setCurrentPage] = useState<SettingsPageKey>("general");
  const personaSettings = createPersonaSettingsFromProfile(props.profile);

  const navItems: SettingsNavItem[] = [
    { key: "general", label: t(displayLanguage, "general") },
    { key: "petBehavior", label: t(displayLanguage, "petBehavior") },
    { key: "model", label: t(displayLanguage, "model") },
    { key: "persona", label: t(displayLanguage, "persona") },
    { key: "live2dModel", label: t(displayLanguage, "live2dModel") },
    { key: "notifications", label: t(displayLanguage, "notifications") },
    { key: "memoryDebug", label: t(displayLanguage, "memoryDebug") }
  ];

  return (
    <main className="settings-window" aria-label={t(displayLanguage, "settings")}>
      <header className="settings-window-header">
        <div className="settings-window-copy">
          <span className="settings-window-kicker">{t(displayLanguage, "settingsWindowEyebrow")}</span>
          <h1>{t(displayLanguage, "settings")}</h1>
          <p>{t(displayLanguage, "productSettingsSubtitle")}</p>
        </div>
      </header>
      <SettingsCenterShell
        displayLanguage={displayLanguage}
        currentPage={currentPage}
        items={navItems}
        onSelect={setCurrentPage}
      >
        {currentPage === "general" ? (
          <GeneralSettingsPage
            petSettings={props.petSettings}
            onUpdatePetSettings={props.onUpdatePetSettings}
          />
        ) : null}
        {currentPage === "petBehavior" ? (
          <PetBehaviorSettingsPage
            petSettings={props.petSettings}
            onUpdatePetSettings={props.onUpdatePetSettings}
          />
        ) : null}
        {currentPage === "model" ? (
          <ModelSettingsPage
            petSettings={props.petSettings}
            appConfig={props.appConfig}
            modelHealth={props.modelHealth}
            onUpdateTaskRunnerSettings={props.onUpdateTaskRunnerSettings}
            onUpdateModelProviderSettings={props.onUpdateModelProviderSettings}
            onOpenConfigFile={props.onOpenConfigFile}
            onTestModelConnection={props.onTestModelConnection}
          />
        ) : null}
        {currentPage === "persona" ? (
          <PersonaSettingsPage
            petSettings={props.petSettings}
            personaSettings={personaSettings}
            onUpdatePersonaSettings={props.onUpdatePersonaSettings}
          />
        ) : null}
        {currentPage === "live2dModel" ? (
          <Live2DModelSettingsPage
            petSettings={props.petSettings}
            live2dModelState={props.live2dModelState}
            onSelectModel={props.onSetCurrentLive2DModel}
          />
        ) : null}
        {currentPage === "notifications" ? (
          <NotificationSettingsPage
            petSettings={props.petSettings}
            appConfig={props.appConfig}
            notificationSettings={props.notificationSettings}
            onUpdateNotificationSettings={props.onUpdateNotificationSettings}
            onUpdateProactiveSettings={props.onUpdateProactiveSettings}
          />
        ) : null}
        {currentPage === "memoryDebug" ? (
          <MemoryDebugSettingsPage
            petSettings={props.petSettings}
            snapshot={props.memoryDebugSnapshot}
            onRefresh={props.onLoadMemoryDebugSnapshot}
          />
        ) : null}
      </SettingsCenterShell>
    </main>
  );
}
