import { useEffect, useState } from "react";
import type { PersonaSettings } from "../../../../../shared/contracts/personaSettings";
import type { PetSettings } from "../../../../../shared/schemas/petSettings";
import { t } from "../../../i18n";
import { SettingsPageLead } from "../SettingsPageLead";

export function PersonaSettingsPage({
  petSettings,
  personaSettings,
  onUpdatePersonaSettings
}: {
  petSettings: PetSettings;
  personaSettings: PersonaSettings;
  onUpdatePersonaSettings(settings: PersonaSettings): Promise<void>;
}) {
  const displayLanguage = petSettings.displayLanguage;
  const [draft, setDraft] = useState<PersonaSettings>(personaSettings);
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    setDraft(personaSettings);
  }, [personaSettings]);

  return (
    <section className="settings-page-section" aria-label={t(displayLanguage, "persona")}>
      <div className="settings-page-section-header">
        <SettingsPageLead
          title={t(displayLanguage, "persona")}
          hint={t(displayLanguage, "personaSettingsHint")}
        />
        <button
          type="button"
          className="settings-config-button"
          aria-label={showAdvanced ? t(displayLanguage, "closeAdvancedPersonaEditing") : t(displayLanguage, "openAdvancedPersonaEditing")}
          onClick={() => setShowAdvanced((current) => !current)}
        >
          {showAdvanced ? t(displayLanguage, "closeAdvancedPersonaEditing") : t(displayLanguage, "openAdvancedPersonaEditing")}
        </button>
      </div>

      <label className="settings-page-field">
        <span>{t(displayLanguage, "personaName")}</span>
        <input
          type="text"
          value={draft.name}
          aria-label={t(displayLanguage, "personaName")}
          onInput={(event) => setDraft((current) => ({ ...current, name: event.currentTarget.value }))}
        />
      </label>

      <label className="settings-page-field">
        <span>{t(displayLanguage, "personaDescription")}</span>
        <textarea
          value={draft.shortPersonaDescription}
          aria-label={t(displayLanguage, "personaDescription")}
          onInput={(event) =>
            setDraft((current) => ({ ...current, shortPersonaDescription: event.currentTarget.value }))
          }
        />
      </label>

      <label className="settings-page-field">
        <span>{t(displayLanguage, "personaTone")}</span>
        <input
          type="text"
          value={draft.tone}
          aria-label={t(displayLanguage, "personaTone")}
          onInput={(event) => setDraft((current) => ({ ...current, tone: event.currentTarget.value }))}
        />
      </label>

      <label className="settings-page-field">
        <span>{t(displayLanguage, "personaRelationship")}</span>
        <input
          type="text"
          value={draft.relationshipStyle}
          aria-label={t(displayLanguage, "personaRelationship")}
          onInput={(event) =>
            setDraft((current) => ({ ...current, relationshipStyle: event.currentTarget.value }))
          }
        />
      </label>

      <label className="settings-page-field">
        <span>{t(displayLanguage, "personaProactiveLevel")}</span>
        <select
          value={draft.proactiveLevel}
          aria-label={t(displayLanguage, "personaProactiveLevel")}
          onChange={(event) =>
            setDraft((current) => ({
              ...current,
              proactiveLevel: event.currentTarget.value as PersonaSettings["proactiveLevel"]
            }))
          }
        >
          <option value="low">{t(displayLanguage, "personaProactiveLow")}</option>
          <option value="balanced">{t(displayLanguage, "personaProactiveBalanced")}</option>
          <option value="high">{t(displayLanguage, "personaProactiveHigh")}</option>
        </select>
      </label>

      {showAdvanced ? (
        <div className="settings-persona-advanced">
          <label className="settings-page-field">
            <span>{t(displayLanguage, "personaPrinciples")}</span>
            <textarea
              value={draft.principles.join("\n")}
              aria-label={t(displayLanguage, "personaPrinciples")}
              onInput={(event) =>
                setDraft((current) => ({
                  ...current,
                  principles: splitLines(event.currentTarget.value)
                }))
              }
            />
          </label>

          <label className="settings-page-field">
            <span>{t(displayLanguage, "personaTaboos")}</span>
            <textarea
              value={draft.taboos.join("\n")}
              aria-label={t(displayLanguage, "personaTaboos")}
              onInput={(event) =>
                setDraft((current) => ({
                  ...current,
                  taboos: splitLines(event.currentTarget.value)
                }))
              }
            />
          </label>

          <label className="settings-page-field">
            <span>{t(displayLanguage, "personaReminderStyle")}</span>
            <textarea
              value={draft.reminderStyle}
              aria-label={t(displayLanguage, "personaReminderStyle")}
              onInput={(event) =>
                setDraft((current) => ({ ...current, reminderStyle: event.currentTarget.value }))
              }
            />
          </label>

          <label className="settings-page-field">
            <span>{t(displayLanguage, "personaReplyPreference")}</span>
            <textarea
              value={draft.replyPreference}
              aria-label={t(displayLanguage, "personaReplyPreference")}
              onInput={(event) =>
                setDraft((current) => ({ ...current, replyPreference: event.currentTarget.value }))
              }
            />
          </label>
        </div>
      ) : null}

      <div className="settings-page-actions">
        <button
          type="button"
          className="settings-config-button"
          aria-label={t(displayLanguage, "savePersonaSettings")}
          onClick={() => void onUpdatePersonaSettings(draft)}
        >
          {t(displayLanguage, "savePersonaSettings")}
        </button>
      </div>
    </section>
  );
}

function splitLines(value: string): string[] {
  return value
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter(Boolean);
}
