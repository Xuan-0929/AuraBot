import type { PublicAppConfig, PublicModelRoleConfig } from "../../../../shared/contracts/settings";
import type { PetSettings, PetSettingsPatch } from "../../../../shared/schemas/petSettings";
import { t, type UiTextKey } from "../../i18n";

export interface SettingsWindowProps {
  petSettings: PetSettings;
  appConfig?: PublicAppConfig;
  onUpdatePetSettings(patch: PetSettingsPatch): Promise<void>;
  onOpenConfigFile(): Promise<void>;
}

const MODEL_CONFIG_ROWS: Array<{
  key: keyof PublicAppConfig;
  labelKey: UiTextKey;
}> = [
  {
    key: "chatModel",
    labelKey: "chatModel"
  },
  {
    key: "gateModel",
    labelKey: "gateModel"
  },
  {
    key: "memoryModel",
    labelKey: "memoryModel"
  }
];

export function SettingsWindow({ petSettings, appConfig, onUpdatePetSettings, onOpenConfigFile }: SettingsWindowProps) {
  const displayLanguage = petSettings.displayLanguage;

  return (
    <main className="settings-window" aria-label={t(displayLanguage, "settings")}>
      <header className="settings-window-header">
        <div>
          <h1>{t(displayLanguage, "settings")}</h1>
          <p>{t(displayLanguage, "productSettingsSubtitle")}</p>
        </div>
      </header>
      <section className="settings-page-section" aria-label={t(displayLanguage, "general")}>
        <h2>{t(displayLanguage, "general")}</h2>
        <label className="settings-page-field">
          <span>{t(displayLanguage, "language")}</span>
          <select
            value={petSettings.displayLanguage}
            aria-label={t(displayLanguage, "language")}
            onChange={(event) =>
              void onUpdatePetSettings({
                displayLanguage: event.currentTarget.value as PetSettings["displayLanguage"]
              })
            }
          >
            <option value="en">{t(displayLanguage, "english")}</option>
            <option value="zh">{t(displayLanguage, "chinese")}</option>
          </select>
        </label>
      </section>
      <section className="settings-page-section" aria-label={t(displayLanguage, "modelSettings")}>
        <div className="settings-page-section-header">
          <div>
            <h2>{t(displayLanguage, "model")}</h2>
            <p>{t(displayLanguage, "openConfigFileHint")}</p>
          </div>
          <button type="button" className="settings-config-button" onClick={() => void onOpenConfigFile()}>
            {t(displayLanguage, "openConfigFile")}
          </button>
        </div>
        <div className="settings-page-model-list">
          {appConfig ? (
            MODEL_CONFIG_ROWS.map((row) => (
              <ModelConfigCard
                key={row.key}
                label={t(displayLanguage, row.labelKey)}
                config={appConfig[row.key]}
                displayLanguage={displayLanguage}
              />
            ))
          ) : (
            <p className="settings-empty">{t(displayLanguage, "configNotLoaded")}</p>
          )}
        </div>
      </section>
      <section className="settings-page-section" aria-label={t(displayLanguage, "characterSettings")}>
        <h2>{t(displayLanguage, "character")}</h2>
        <label className="settings-page-toggle">
          <input
            type="checkbox"
            checked={petSettings.clickThroughPet}
            aria-label={t(displayLanguage, "clickThroughCharacter")}
            onChange={(event) => void onUpdatePetSettings({ clickThroughPet: event.currentTarget.checked })}
          />
          <span>{t(displayLanguage, "clickThroughCharacter")}</span>
        </label>
      </section>
    </main>
  );
}

function ModelConfigCard({
  label,
  config,
  displayLanguage
}: {
  label: string;
  config: PublicModelRoleConfig;
  displayLanguage: PetSettings["displayLanguage"];
}) {
  return (
    <article className="settings-model-card">
      <div className="settings-model-card-header">
        <strong>{label}</strong>
        <span>{config.enabled ? t(displayLanguage, "enabled") : t(displayLanguage, "disabled")}</span>
      </div>
      <dl>
        <dt>{t(displayLanguage, "provider")}</dt>
        <dd>{config.provider}</dd>
        <dt>{t(displayLanguage, "model")}</dt>
        <dd>{config.model}</dd>
        {config.baseUrl ? (
          <>
            <dt>Base URL</dt>
            <dd>{config.baseUrl}</dd>
          </>
        ) : null}
        <dt>{t(displayLanguage, "apiKey")}</dt>
        <dd>{config.hasApiKey ? t(displayLanguage, "apiKeySaved") : t(displayLanguage, "notSet")}</dd>
      </dl>
    </article>
  );
}
