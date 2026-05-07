import { t } from "../../i18n";
import type { PetSettings } from "../../../../shared/schemas/petSettings";
import type { ReactNode } from "react";

export type SettingsPageKey =
  | "general"
  | "petBehavior"
  | "model"
  | "persona"
  | "live2dModel"
  | "notifications";

export interface SettingsNavItem {
  key: SettingsPageKey;
  label: string;
}

export interface SettingsCenterShellProps {
  displayLanguage: PetSettings["displayLanguage"];
  currentPage: SettingsPageKey;
  items: SettingsNavItem[];
  onSelect(page: SettingsPageKey): void;
  children: ReactNode;
}

export function SettingsCenterShell({
  displayLanguage,
  currentPage,
  items,
  onSelect,
  children
}: SettingsCenterShellProps) {
  return (
    <div className="settings-center">
      <aside className="settings-center-sidebar" aria-label={t(displayLanguage, "settingsNavigation")}>
        <nav className="settings-center-nav" role="tablist" aria-label={t(displayLanguage, "settingsNavigation")}>
          {items.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-label={item.label}
              aria-selected={currentPage === item.key}
              className="settings-center-tab"
              data-active={currentPage === item.key}
              onClick={() => onSelect(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </aside>
      <section className="settings-center-content" aria-label={t(displayLanguage, "settingsContent")}>
        {children}
      </section>
    </div>
  );
}
