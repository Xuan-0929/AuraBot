import type { AgentStatus } from "../../../../shared/contracts/chat";
import type { PublicAppConfig } from "../../../../shared/contracts/settings";
import type { Message } from "../../../../shared/schemas/message";
import type { PetSettings, PetSettingsPatch } from "../../../../shared/schemas/petSettings";
import type { Task } from "../../../../shared/schemas/task";
import { Composer } from "../chat/Composer";
import { MessageList } from "../chat/MessageList";
import { pickConsoleStickyTasks, TaskStickyNotes } from "../tasks/TaskStickyNotes";
import { AgentStatusBar } from "../status/AgentStatusBar";
import { t } from "../../i18n";

export interface ConsoleWindowProps {
  agentStatus: AgentStatus;
  messages: Message[];
  tasks: Task[];
  petSettings: PetSettings;
  appConfig?: PublicAppConfig;
  onUpdatePetSettings(patch: PetSettingsPatch): Promise<void>;
  onOpenSettings(): Promise<void>;
  onSend(content: string): Promise<void>;
  onCancelTask(taskId: string): Promise<void>;
  onDeleteTask(taskId: string): Promise<void>;
}

export function ConsoleWindow({
  agentStatus,
  messages,
  tasks,
  petSettings,
  onSend,
  onCancelTask,
  onDeleteTask
}: ConsoleWindowProps) {
  const consoleTasks = pickConsoleStickyTasks(tasks);

  return (
    <main className="console-window" aria-label={t(petSettings.displayLanguage, "consoleTitle")}>
      <header className="console-header">
        <div className="console-header-copy">
          <div className="console-header-title-row">
            <h1>{t(petSettings.displayLanguage, "consoleDashboardTitle")}</h1>
            <AgentStatusBar status={agentStatus} displayLanguage={petSettings.displayLanguage} />
          </div>
          <p>{t(petSettings.displayLanguage, "consoleSubtitle")}</p>
        </div>
        <aside className="console-task-sticky-layer" aria-label={t(petSettings.displayLanguage, "taskStickyNotes")}>
          <header className="console-task-panel-meta">
            <strong>{t(petSettings.displayLanguage, "consoleTaskViewportTitle")}</strong>
            <span>
              {consoleTasks.length > 0
                ? t(petSettings.displayLanguage, "consoleTaskViewportActive")
                : t(petSettings.displayLanguage, "consoleTaskViewportIdle")}
            </span>
          </header>
          <div className="console-task-viewport">
            <div className="console-task-viewport-list">
              {consoleTasks.length > 0 ? (
                <TaskStickyNotes
                  tasks={consoleTasks}
                  variant="console"
                  showHeader={false}
                  ariaLabel={false}
                  displayLanguage={petSettings.displayLanguage}
                  onCancelTask={(taskId) => {
                    void onCancelTask(taskId);
                  }}
                  onDeleteTask={(taskId) => {
                    void onDeleteTask(taskId);
                  }}
                />
              ) : (
                <div className="console-task-viewport-empty" aria-hidden="true" />
              )}
            </div>
          </div>
        </aside>
      </header>
      <div className="console-body">
        <section className="console-chat" aria-label={t(petSettings.displayLanguage, "mainConversation")}>
          <MessageList messages={messages} displayLanguage={petSettings.displayLanguage} />
          <Composer displayLanguage={petSettings.displayLanguage} onSend={onSend} />
        </section>
      </div>
    </main>
  );
}
