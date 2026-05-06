import type {
  ModelProviderSettingsPatch,
  ModelRoleKey,
  ModelProviderOption,
  PublicAppConfig,
  PublicModelRoleConfig,
  PublicTaskCliRunnerConfig,
  TaskRunnerSettingsPatch
} from "../../../../shared/contracts/settings";
import { MODEL_PROVIDER_OPTIONS as modelProviderOptions } from "../../../../shared/contracts/settings";
import type { PetSettings, PetSettingsPatch } from "../../../../shared/schemas/petSettings";
import { t, type UiTextKey } from "../../i18n";

export interface SettingsWindowProps {
  petSettings: PetSettings;
  appConfig?: PublicAppConfig;
  onUpdatePetSettings(patch: PetSettingsPatch): Promise<void>;
  onUpdateTaskRunnerSettings(runners: TaskRunnerSettingsPatch["runners"]): Promise<void>;
  onUpdateModelProviderSettings(patch: ModelProviderSettingsPatch): Promise<void>;
  onOpenConfigFile(): Promise<void>;
}

const MODEL_CONFIG_ROWS: Array<{
  key: ModelRoleKey;
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

export function SettingsWindow({
  petSettings,
  appConfig,
  onUpdatePetSettings,
  onUpdateTaskRunnerSettings,
  onUpdateModelProviderSettings,
  onOpenConfigFile
}: SettingsWindowProps) {
  const displayLanguage = petSettings.displayLanguage;
  const taskRunners = appConfig?.taskRunner.runners ?? [];
  const updateTaskRunner = (runnerId: PublicTaskCliRunnerConfig["id"], patch: Partial<Pick<PublicTaskCliRunnerConfig, "enabled" | "priority">>) => {
    void onUpdateTaskRunnerSettings(
      taskRunners.map((runner) => ({
        id: runner.id,
        enabled: runner.id === runnerId ? patch.enabled ?? runner.enabled : runner.enabled,
        priority: runner.id === runnerId ? patch.priority ?? runner.priority : runner.priority
      }))
    );
  };

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
                onProviderChange={(provider) =>
                  void onUpdateModelProviderSettings({
                    role: row.key,
                    provider
                  })
                }
              />
            ))
          ) : (
            <p className="settings-empty">{t(displayLanguage, "configNotLoaded")}</p>
          )}
        </div>
      </section>
      <section className="settings-page-section" aria-label={t(displayLanguage, "taskCliSettings")}>
        <div className="settings-page-section-header">
          <div>
            <h2>{t(displayLanguage, "taskCli")}</h2>
            <p>{t(displayLanguage, "taskCliHint")}</p>
          </div>
        </div>
        <div className="settings-task-cli-list">
          {taskRunners.length > 0 ? (
            taskRunners.map((runner) => (
              <TaskCliRunnerCard
                key={runner.id}
                runner={runner}
                displayLanguage={displayLanguage}
                onChange={(patch) => updateTaskRunner(runner.id, patch)}
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

function TaskCliRunnerCard({
  runner,
  displayLanguage,
  onChange
}: {
  runner: PublicTaskCliRunnerConfig;
  displayLanguage: PetSettings["displayLanguage"];
  onChange(patch: Partial<Pick<PublicTaskCliRunnerConfig, "enabled" | "priority">>): void;
}) {
  return (
    <article className="settings-task-cli-card">
      <label className="settings-page-toggle">
        <input
          type="checkbox"
          checked={runner.enabled}
          aria-label={`${t(displayLanguage, "enable")} ${runner.label} ${t(displayLanguage, "taskRunner")}`}
          onChange={(event) => onChange({ enabled: event.currentTarget.checked })}
        />
        <span>{runner.label}</span>
      </label>
      <label className="settings-page-field settings-task-cli-priority">
        <span>{t(displayLanguage, "priority")}</span>
        <input
          type="number"
          min={1}
          max={99}
          value={runner.priority}
          aria-label={`${runner.label} ${t(displayLanguage, "taskRunnerPriority")}`}
          onInput={(event) =>
            onChange({
              priority: Math.max(1, Number.parseInt(event.currentTarget.value, 10) || runner.priority)
            })
          }
        />
      </label>
      <p>{runner.command ? runner.command : t(displayLanguage, "notSet")}</p>
    </article>
  );
}

function ModelConfigCard({
  label,
  config,
  displayLanguage,
  onProviderChange
}: {
  label: string;
  config: PublicModelRoleConfig;
  displayLanguage: PetSettings["displayLanguage"];
  onProviderChange(provider: ModelProviderOption): void;
}) {
  return (
    <article className="settings-model-card">
      <div className="settings-model-card-header">
        <strong>{label}</strong>
        <span>{config.enabled ? t(displayLanguage, "enabled") : t(displayLanguage, "disabled")}</span>
      </div>
      <dl>
        <dt>{t(displayLanguage, "provider")}</dt>
        <dd>
          <select
            className="settings-model-provider-select"
            value={toModelProviderOption(config.provider)}
            aria-label={`${label} ${t(displayLanguage, "provider")}`}
            onChange={(event) => onProviderChange(event.currentTarget.value as ModelProviderOption)}
          >
            {modelProviderOptions.map((provider) => (
              <option key={provider} value={provider}>
                {getModelProviderLabel(provider, displayLanguage)}
              </option>
            ))}
          </select>
        </dd>
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

function toModelProviderOption(provider: string): ModelProviderOption {
  return modelProviderOptions.includes(provider as ModelProviderOption)
    ? (provider as ModelProviderOption)
    : "local-stub";
}

function getModelProviderLabel(
  provider: ModelProviderOption,
  displayLanguage: PetSettings["displayLanguage"]
): string {
  if (provider === "openai-compatible") {
    return t(displayLanguage, "providerOpenAICompatible");
  }

  if (provider === "langchain-openai") {
    return t(displayLanguage, "providerLangChainOpenAI");
  }

  return t(displayLanguage, "providerLocalFallback");
}
