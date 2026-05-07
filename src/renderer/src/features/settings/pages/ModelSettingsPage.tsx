import type {
  PublicModelHealth,
  PublicModelHealthMap,
  ModelProviderSettingsPatch,
  ModelRoleKey,
  ModelProviderOption,
  PublicAppConfig,
  PublicModelRoleConfig,
  PublicTaskCliRunnerConfig,
  TaskRunnerSettingsPatch
} from "../../../../../shared/contracts/settings";
import { MODEL_PROVIDER_OPTIONS as modelProviderOptions } from "../../../../../shared/contracts/settings";
import type { PetSettings } from "../../../../../shared/schemas/petSettings";
import { t, type UiTextKey } from "../../../i18n";

const MODEL_CONFIG_ROWS: Array<{
  key: ModelRoleKey;
  labelKey: UiTextKey;
}> = [
  { key: "chatModel", labelKey: "chatModel" },
  { key: "gateModel", labelKey: "gateModel" },
  { key: "memoryModel", labelKey: "memoryModel" }
];

export function ModelSettingsPage({
  petSettings,
  appConfig,
  modelHealth,
  onUpdateTaskRunnerSettings,
  onUpdateModelProviderSettings,
  onOpenConfigFile,
  onTestModelConnection
}: {
  petSettings: PetSettings;
  appConfig?: PublicAppConfig;
  modelHealth?: PublicModelHealthMap;
  onUpdateTaskRunnerSettings(runners: TaskRunnerSettingsPatch["runners"]): Promise<void>;
  onUpdateModelProviderSettings(patch: ModelProviderSettingsPatch): Promise<void>;
  onOpenConfigFile(): Promise<void>;
  onTestModelConnection(role: ModelRoleKey): Promise<void>;
}) {
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
    <>
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
                health={modelHealth?.[row.key]}
                displayLanguage={displayLanguage}
                onProviderChange={(provider) =>
                  void onUpdateModelProviderSettings({
                    role: row.key,
                    provider
                  })
                }
                onTestConnection={() => void onTestModelConnection(row.key)}
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
    </>
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
  health,
  displayLanguage,
  onProviderChange,
  onTestConnection
}: {
  label: string;
  config: PublicModelRoleConfig;
  health?: PublicModelHealth;
  displayLanguage: PetSettings["displayLanguage"];
  onProviderChange(provider: ModelProviderOption): void;
  onTestConnection(): void;
}) {
  return (
    <article className="settings-model-card">
      <div className="settings-model-card-header">
        <strong>{label}</strong>
        <span>{config.enabled ? t(displayLanguage, "enabled") : t(displayLanguage, "disabled")}</span>
      </div>
      <div className="settings-model-card-actions">
        <div className="settings-model-health">
          <span className="settings-model-health-label">{t(displayLanguage, "modelHealth")}</span>
          <span className="settings-model-health-value">{modelHealthLabel(health, displayLanguage)}</span>
        </div>
        <button
          type="button"
          className="settings-model-test-button"
          aria-label={testConnectionAriaLabel(label, displayLanguage)}
          onClick={onTestConnection}
          disabled={health?.state === "testing"}
        >
          {t(displayLanguage, "testConnection")}
        </button>
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
        {health?.lastErrorCode ? (
          <>
            <dt>{t(displayLanguage, "modelStateError")}</dt>
            <dd>{modelHealthErrorLabel(health.lastErrorCode, displayLanguage)}</dd>
          </>
        ) : null}
      </dl>
    </article>
  );
}

function toModelProviderOption(provider: string): ModelProviderOption {
  return modelProviderOptions.includes(provider as ModelProviderOption)
    ? (provider as ModelProviderOption)
    : "openai-compatible";
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

  return t(displayLanguage, "providerOpenAICompatible");
}

function modelHealthLabel(
  health: PublicModelHealth | undefined,
  displayLanguage: PetSettings["displayLanguage"]
): string {
  if (!health) {
    return t(displayLanguage, "modelStateNotConfigured");
  }

  switch (health.state) {
    case "connected":
      return t(displayLanguage, "modelStateConnected");
    case "configured":
      return t(displayLanguage, "modelStateConfigured");
    case "testing":
      return t(displayLanguage, "modelStateTesting");
    case "error":
      return t(displayLanguage, "modelStateError");
    case "using_test_double":
      return t(displayLanguage, "modelStateUsingTestDouble");
    default:
      return t(displayLanguage, "modelStateNotConfigured");
  }
}

function modelHealthErrorLabel(
  code: PublicModelHealth["lastErrorCode"],
  displayLanguage: PetSettings["displayLanguage"]
): string {
  switch (code) {
    case "unsupported_provider":
      return t(displayLanguage, "modelErrorUnsupportedProvider");
    case "authentication_failed":
      return t(displayLanguage, "modelErrorAuthenticationFailed");
    case "timeout":
      return t(displayLanguage, "modelErrorTimeout");
    case "network_error":
      return t(displayLanguage, "modelErrorNetwork");
    case "malformed_response":
      return t(displayLanguage, "modelErrorMalformedResponse");
    case "invocation_failed":
      return t(displayLanguage, "modelErrorInvocationFailed");
    default:
      return t(displayLanguage, "modelErrorMissingConfiguration");
  }
}

function testConnectionAriaLabel(label: string, displayLanguage: PetSettings["displayLanguage"]): string {
  if (displayLanguage === "zh") {
    return `测试${label}连接`;
  }

  return `Test ${label} connection`;
}
