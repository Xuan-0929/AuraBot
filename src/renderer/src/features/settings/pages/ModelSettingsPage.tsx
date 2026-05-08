import type {
  PublicModelHealth,
  PublicModelHealthMap,
  ModelProviderSettingsPatch,
  ModelRoleKey,
  ModelProviderOption,
  PublicAppConfig,
  PublicModelRoleConfig
} from "../../../../../shared/contracts/settings";
import { MODEL_PROVIDER_OPTIONS as modelProviderOptions } from "../../../../../shared/contracts/settings";
import type { PetSettings } from "../../../../../shared/schemas/petSettings";
import { t, type UiTextKey } from "../../../i18n";
import { SettingsPageLead } from "../SettingsPageLead";

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
  onUpdateModelProviderSettings,
  onOpenConfigFile,
  onTestModelConnection
}: {
  petSettings: PetSettings;
  appConfig?: PublicAppConfig;
  modelHealth?: PublicModelHealthMap;
  onUpdateModelProviderSettings(patch: ModelProviderSettingsPatch): Promise<void>;
  onOpenConfigFile(): Promise<void>;
  onTestModelConnection(role: ModelRoleKey): Promise<void>;
}) {
  const displayLanguage = petSettings.displayLanguage;

  return (
    <section className="settings-page-section" aria-label={t(displayLanguage, "modelSettings")}>
      <div className="settings-page-section-header">
        <SettingsPageLead
          title={t(displayLanguage, "model")}
          hint={t(displayLanguage, "modelSettingsHint")}
        />
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
