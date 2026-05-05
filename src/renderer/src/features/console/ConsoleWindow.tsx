import { useState } from "react";
import type { AgentStatus } from "../../../../shared/contracts/chat";
import type { MemoryDebugSnapshot } from "../../../../shared/contracts/ipc";
import type { Message } from "../../../../shared/schemas/message";
import type { PetSettings, PetSettingsPatch } from "../../../../shared/schemas/petSettings";
import type { Task } from "../../../../shared/schemas/task";
import { Composer } from "../chat/Composer";
import { MessageList } from "../chat/MessageList";
import { TaskStatusOverlay } from "../tasks/TaskStatusOverlay";
import { AgentStatusBar } from "../status/AgentStatusBar";
import { PetSettingsPanel } from "../settings/PetSettingsPanel";
import { MemoryDebugPanel } from "../memory/MemoryDebugPanel";

export interface ConsoleWindowProps {
  agentStatus: AgentStatus;
  messages: Message[];
  tasks: Task[];
  petSettings: PetSettings;
  memoryDebugSnapshot?: MemoryDebugSnapshot;
  onUpdatePetSettings(patch: PetSettingsPatch): Promise<void>;
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
  memoryDebugSnapshot,
  onUpdatePetSettings,
  onLoadMemoryDebugSnapshot,
  onSend,
  onCancelTask,
  onDeleteTask
}: ConsoleWindowProps) {
  const [memoryDebugExpanded, setMemoryDebugExpanded] = useState(false);

  return (
    <main className="console-window" aria-label="AuraBot console window">
      <header className="console-header">
        <div>
          <h1>AuraBot Console</h1>
          <p>Chat here when the desktop companion needs a full thread.</p>
        </div>
        <div className="console-header-tools">
          <AgentStatusBar status={agentStatus} />
          <PetSettingsPanel settings={petSettings} onUpdate={onUpdatePetSettings} />
        </div>
      </header>
      <TaskStatusOverlay tasks={tasks} onCancelTask={onCancelTask} onDeleteTask={onDeleteTask} />
      <MemoryDebugPanel
        snapshot={memoryDebugSnapshot}
        expanded={memoryDebugExpanded}
        onToggle={() => setMemoryDebugExpanded((current) => !current)}
        onRefresh={onLoadMemoryDebugSnapshot}
      />
      <section className="console-chat" aria-label="Main conversation">
        <MessageList messages={messages} />
        <Composer onSend={onSend} />
      </section>
    </main>
  );
}
