import { useEffect, useId, useState } from "react";
import type { PublicAppConfig } from "../../../../shared/contracts/settings";
import type { PetSettings, PetSettingsPatch } from "../../../../shared/schemas/petSettings";
import { t } from "../../i18n";

export interface PetSettingsPanelProps {
  settings: PetSettings;
  appConfig?: PublicAppConfig;
  onUpdate(patch: PetSettingsPatch): Promise<void>;
  onOpenSettings(): Promise<void>;
}

export function PetSettingsPanel({ settings, onUpdate, onOpenSettings }: PetSettingsPanelProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuId = useId();
  const displayLanguage = settings.displayLanguage;

  useEffect(() => {
    if (!menuOpen) {
      return undefined;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
      }
    };

    document.addEventListener("keydown", handleKeyDown);

    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [menuOpen]);

  return (
    <section className="settings-menu-shell" aria-label={t(displayLanguage, "settings")}>
      <button
        type="button"
        className="settings-menu-button"
        aria-label={t(displayLanguage, "openSettingsMenu")}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        aria-controls={menuId}
        onClick={() => setMenuOpen((current) => !current)}
      >
        {t(displayLanguage, "settings")}
      </button>
      {menuOpen ? (
        <div id={menuId} className="settings-menu" role="menu" aria-label={t(displayLanguage, "settingsMenu")}>
          <section className="settings-menu-section" aria-label={t(displayLanguage, "quickSettings")}>
            <h2>{t(displayLanguage, "quickSettings")}</h2>
            <button
              className="settings-full-button"
              type="button"
              aria-label={t(displayLanguage, "openFullSettings")}
              onClick={() => void onOpenSettings()}
            >
              {t(displayLanguage, "openFullSettings")}
            </button>
            <label className="settings-field">
              <span>{t(displayLanguage, "language")}</span>
              <select
                value={settings.displayLanguage}
                aria-label={t(displayLanguage, "language")}
                onChange={(event) =>
                  void onUpdate({ displayLanguage: event.currentTarget.value as PetSettings["displayLanguage"] })
                }
              >
                <option value="en">{t(displayLanguage, "english")}</option>
                <option value="zh">{t(displayLanguage, "chinese")}</option>
              </select>
            </label>
            <label className="settings-toggle">
              <input
                type="checkbox"
                checked={settings.clickThroughPet}
                aria-label={t(displayLanguage, "clickThroughCharacter")}
                onChange={(event) => void onUpdate({ clickThroughPet: event.currentTarget.checked })}
              />
              <span>{t(displayLanguage, "clickThroughCharacter")}</span>
            </label>
          </section>
        </div>
      ) : null}
    </section>
  );
}
