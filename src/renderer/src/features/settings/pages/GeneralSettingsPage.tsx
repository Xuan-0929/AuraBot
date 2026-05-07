import { t } from "../../../i18n";
import type { PetSettings, PetSettingsPatch } from "../../../../../shared/schemas/petSettings";

export function GeneralSettingsPage({
  petSettings,
  onUpdatePetSettings
}: {
  petSettings: PetSettings;
  onUpdatePetSettings(patch: PetSettingsPatch): Promise<void>;
}) {
  const displayLanguage = petSettings.displayLanguage;

  return (
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
  );
}
