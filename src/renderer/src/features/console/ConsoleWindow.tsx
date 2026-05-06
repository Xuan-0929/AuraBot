import { useState } from "react";
import type { AgentStatus } from "../../../../shared/contracts/chat";
import type { MemoryDebugSnapshot } from "../../../../shared/contracts/ipc";
import type { PublicAppConfig } from "../../../../shared/contracts/settings";
import type { Message } from "../../../../shared/schemas/message";
import type { PetSettings, PetSettingsPatch } from "../../../../shared/schemas/petSettings";
import type { Task } from "../../../../shared/schemas/task";
import { Composer } from "../chat/Composer";
import { MessageList } from "../chat/MessageList";
import { TaskStatusOverlay } from "../tasks/TaskStatusOverlay";
import { AgentStatusBar } from "../status/AgentStatusBar";
import { PetSettingsPanel } from "../settings/PetSettingsPanel";
import { MemoryDebugPanel } from "../memory/MemoryDebugPanel";
import { t } from "../../i18n";

export interface ConsoleWindowProps {
  agentStatus: AgentStatus;
  messages: Message[];
  tasks: Task[];
  petSettings: PetSettings;
  appConfig?: PublicAppConfig;
  memoryDebugSnapshot?: MemoryDebugSnapshot;
  onUpdatePetSettings(patch: PetSettingsPatch): Promise<void>;
  onOpenSettings(): Promise<void>;
  onLoadMemoryDebugSnapshot(): Promise<void>;
  onSend(content: string): Promise<void>;
  onCancelTask(taskId: string): Promise<void>;
  onDeleteTask(taskId: string): Promise<void>;
}

export function ConsoleWindow({
  agentStatus,
  messages,
  tasks,
  petSettings,
  appConfig,
  memoryDebugSnapshot,
  onUpdatePetSettings,
  onOpenSettings,
  onLoadMemoryDebugSnapshot,
  onSend,
  onCancelTask,
  onDeleteTask
}: ConsoleWindowProps) {
  const [memoryDebugExpanded, setMemoryDebugExpanded] = useState(false);

  return (
    <main className="console-window" aria-label={t(petSettings.displayLanguage, "consoleTitle")}>
      <header className="console-header">
        <div>
          <h1>{t(petSettings.displayLanguage, "consoleTitle")}</h1>
          <p>{t(petSettings.displayLanguage, "consoleSubtitle")}</p>
        </div>
        <div className="console-header-tools">
          <AgentStatusBar status={agentStatus} displayLanguage={petSettings.displayLanguage} />
          <PetSettingsPanel
            settings={petSettings}
            appConfig={appConfig}
            onUpdate={onUpdatePetSettings}
            onOpenSettings={onOpenSettings}
          />
        </div>
      </header>
      <TaskStatusOverlay
        tasks={tasks}
        displayLanguage={petSettings.displayLanguage}
        onCancelTask={onCancelTask}
        onDeleteTask={onDeleteTask}
      />
      <MemoryDebugPanel
        snapshot={memoryDebugSnapshot}
        expanded={memoryDebugExpanded}
        displayLanguage={petSettings.displayLanguage}
        onToggle={() => setMemoryDebugExpanded((current) => !current)}
        onRefresh={onLoadMemoryDebugSnapshot}
      />
      <section className="console-chat" aria-label={t(petSettings.displayLanguage, "mainConversation")}>
        <MessageList messages={messages} displayLanguage={petSettings.displayLanguage} />
        <Composer displayLanguage={petSettings.displayLanguage} onSend={onSend} />
      </section>
    </main>
  );
}
