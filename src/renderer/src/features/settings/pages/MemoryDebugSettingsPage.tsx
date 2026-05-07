import { useEffect } from "react";
import type { MemoryDebugSnapshot } from "../../../../../shared/contracts/ipc";
import type { PetSettings } from "../../../../../shared/schemas/petSettings";
import { t } from "../../../i18n";
import { MemoryDebugContent } from "../../memory/MemoryDebugPanel";
import { SettingsPageLead } from "../SettingsPageLead";

export function MemoryDebugSettingsPage({
  petSettings,
  snapshot,
  onRefresh
}: {
  petSettings: PetSettings;
  snapshot?: MemoryDebugSnapshot;
  onRefresh(): Promise<void>;
}) {
  const displayLanguage = petSettings.displayLanguage;

  useEffect(() => {
    void onRefresh();
  }, [onRefresh]);

  return (
    <section className="settings-page-section" aria-label={t(displayLanguage, "memoryDebug")}>
      <div className="settings-page-section-header">
        <SettingsPageLead
          title={t(displayLanguage, "memoryDebug")}
          hint={t(displayLanguage, "memoryDebugSettingsHint")}
        />
        <button
          type="button"
          className="settings-config-button"
          aria-label={t(displayLanguage, "refresh")}
          onClick={() => void onRefresh()}
        >
          {t(displayLanguage, "refresh")}
        </button>
      </div>
      <div className="memory-debug-panel memory-debug-panel-page" aria-label={t(displayLanguage, "memoryDebugPanel")}>
        <MemoryDebugContent snapshot={snapshot} displayLanguage={displayLanguage} />
      </div>
    </section>
  );
}
