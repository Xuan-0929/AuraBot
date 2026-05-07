import { t } from "../../../i18n";
import type { PetSettings, PetSettingsPatch } from "../../../../../shared/schemas/petSettings";

export function PetBehaviorSettingsPage({
  petSettings,
  onUpdatePetSettings
}: {
  petSettings: PetSettings;
  onUpdatePetSettings(patch: PetSettingsPatch): Promise<void>;
}) {
  const displayLanguage = petSettings.displayLanguage;

  return (
    <section className="settings-page-section" aria-label={t(displayLanguage, "petBehavior")}>
      <h2>{t(displayLanguage, "petBehavior")}</h2>
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
  );
}
