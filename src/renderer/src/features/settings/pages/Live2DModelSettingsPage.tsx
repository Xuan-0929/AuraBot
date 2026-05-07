import { Live2DModelCanvas } from "../../live2d/Live2DModelCanvas";
import type { Live2DModelState } from "../../../../../shared/contracts/live2dModel";
import { t } from "../../../i18n";
import type { PetSettings } from "../../../../../shared/schemas/petSettings";
import { SettingsPageLead } from "../SettingsPageLead";

export function Live2DModelSettingsPage({
  petSettings,
  live2dModelState,
  onSelectModel
}: {
  petSettings: PetSettings;
  live2dModelState?: Live2DModelState;
  onSelectModel(modelId: string): Promise<void>;
}) {
  const displayLanguage = petSettings.displayLanguage;
  const currentModel = live2dModelState?.models.find((model) => model.id === live2dModelState.currentModelId);

  return (
    <section className="settings-page-section" aria-label={t(displayLanguage, "live2dModel")}>
      <div className="settings-page-section-header">
        <SettingsPageLead
          title={t(displayLanguage, "live2dModel")}
          hint={t(displayLanguage, "live2dModelSettingsHint")}
        />
      </div>
      {currentModel ? (
        <div className="settings-live2d-layout">
          <div className="settings-live2d-preview-card">
            <div className="settings-live2d-preview">
              <Live2DModelCanvas
                modelPath={live2dModelState?.currentModelPath ?? currentModel.manifestPath}
                agentStatus="idle"
                compact
              />
            </div>
            <dl className="settings-live2d-meta">
              <dt>{t(displayLanguage, "live2dCurrentModel")}</dt>
              <dd>{currentModel.displayName}</dd>
              <dt>{t(displayLanguage, "live2dModelPath")}</dt>
              <dd>{currentModel.manifestPath}</dd>
            </dl>
          </div>
          <div className="settings-live2d-list">
            <h3>{t(displayLanguage, "live2dLocalModels")}</h3>
            {live2dModelState?.models.map((model) => (
              <button
                key={model.id}
                type="button"
                className="settings-live2d-model-item"
                data-active={model.id === live2dModelState.currentModelId}
                aria-label={`${displayLanguage === "zh" ? "切换到" : "Switch to"} ${model.displayName} ${displayLanguage === "zh" ? "模型" : "model"}`}
                onClick={() => void onSelectModel(model.id)}
              >
                <strong>{model.displayName}</strong>
                <span>{model.manifestPath}</span>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <p className="settings-empty">{t(displayLanguage, "live2dModelSettingsComingSoon")}</p>
      )}
    </section>
  );
}
