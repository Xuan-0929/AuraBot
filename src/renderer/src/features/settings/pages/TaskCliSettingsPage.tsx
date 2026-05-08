import type {
  PublicAppConfig,
  PublicTaskCliRunnerConfig,
  TaskRunnerSettingsPatch
} from "../../../../../shared/contracts/settings";
import type { PetSettings } from "../../../../../shared/schemas/petSettings";
import { t } from "../../../i18n";
import { SettingsPageLead } from "../SettingsPageLead";

const DEFAULT_TASK_TIMEOUT_MS = 300000;

export function TaskCliSettingsPage({
  petSettings,
  appConfig,
  onUpdateTaskRunnerSettings
}: {
  petSettings: PetSettings;
  appConfig?: PublicAppConfig;
  onUpdateTaskRunnerSettings(runners: TaskRunnerSettingsPatch["runners"]): Promise<void>;
}) {
  const displayLanguage = petSettings.displayLanguage;
  const taskRunners = appConfig?.taskRunner.runners ?? [];

  const updateTaskRunner = (
    runnerId: PublicTaskCliRunnerConfig["id"],
    patch: Partial<Pick<PublicTaskCliRunnerConfig, "enabled" | "priority" | "timeoutMs">>
  ) => {
    void onUpdateTaskRunnerSettings(
      taskRunners.map((runner) => ({
        id: runner.id,
        enabled: runner.id === runnerId ? patch.enabled ?? runner.enabled : runner.enabled,
        priority: runner.id === runnerId ? patch.priority ?? runner.priority : runner.priority,
        timeoutMs:
          runner.id === runnerId
            ? patch.timeoutMs ?? runner.timeoutMs ?? DEFAULT_TASK_TIMEOUT_MS
            : runner.timeoutMs ?? DEFAULT_TASK_TIMEOUT_MS
      }))
    );
  };

  return (
    <section className="settings-page-section" aria-label={t(displayLanguage, "taskCliSettings")}>
      <SettingsPageLead
        title={t(displayLanguage, "taskCli")}
        hint={t(displayLanguage, "taskCliHint")}
      />
      <p className="settings-page-boundary-note">{t(displayLanguage, "taskCliTimeoutHint")}</p>
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
  );
}

function TaskCliRunnerCard({
  runner,
  displayLanguage,
  onChange
}: {
  runner: PublicTaskCliRunnerConfig;
  displayLanguage: PetSettings["displayLanguage"];
  onChange(patch: Partial<Pick<PublicTaskCliRunnerConfig, "enabled" | "priority" | "timeoutMs">>): void;
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
      <label className="settings-page-field settings-task-cli-timeout">
        <span>{t(displayLanguage, "taskTimeLimit")}</span>
        <input
          type="number"
          min={1}
          max={240}
          value={toTimeoutMinutes(runner.timeoutMs)}
          aria-label={`${runner.label} ${t(displayLanguage, "taskTimeLimitMinutesAria")}`}
          onInput={(event) =>
            onChange({
              timeoutMs:
                Math.max(1, Number.parseInt(event.currentTarget.value, 10) || toTimeoutMinutes(runner.timeoutMs)) * 60000
            })
          }
        />
      </label>
      <p>
        {t(displayLanguage, "taskCliCommand")}: {runner.command ?? t(displayLanguage, "notSet")} ·{" "}
        {t(displayLanguage, "taskCliWorkingDirectory")}: {runner.workingDirectory ?? t(displayLanguage, "notSet")}
      </p>
    </article>
  );
}

function toTimeoutMinutes(timeoutMs: number | undefined): number {
  return Math.max(1, Math.round((timeoutMs ?? DEFAULT_TASK_TIMEOUT_MS) / 60000));
}
