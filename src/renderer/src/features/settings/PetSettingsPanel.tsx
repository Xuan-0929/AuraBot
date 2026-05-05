import type { PetSettings, PetSettingsPatch } from "../../../../shared/schemas/petSettings";

export interface PetSettingsPanelProps {
  settings: PetSettings;
  onUpdate(patch: PetSettingsPatch): Promise<void>;
}

export function PetSettingsPanel({ settings, onUpdate }: PetSettingsPanelProps) {
  return (
    <section className="pet-settings-panel" aria-label="Pet settings">
      <span className="pet-settings-title">Pet</span>
      <label className="pet-settings-option">
        <span>Language</span>
        <select
          value={settings.displayLanguage}
          aria-label="Display language"
          onChange={(event) => void onUpdate({ displayLanguage: event.currentTarget.value as PetSettings["displayLanguage"] })}
        >
          <option value="en">English</option>
          <option value="zh">中文</option>
        </select>
      </label>
      <label className="pet-settings-option">
        <input
          type="checkbox"
          checked={settings.clickThroughPet}
          aria-label="Click through character"
          onChange={(event) => void onUpdate({ clickThroughPet: event.currentTarget.checked })}
        />
        <span>Click through</span>
      </label>
    </section>
  );
}
