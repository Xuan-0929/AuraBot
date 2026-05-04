import type { AgentStatus } from "../../../../shared/contracts/chat";
import type { Message } from "../../../../shared/schemas/message";
import type { Task } from "../../../../shared/schemas/task";
import { Composer } from "../chat/Composer";
import { MessageList } from "../chat/MessageList";
import { TaskStatusOverlay } from "../tasks/TaskStatusOverlay";
import { AgentStatusBar } from "../status/AgentStatusBar";

export interface ConsoleWindowProps {
  agentStatus: AgentStatus;
  messages: Message[];
  tasks: Task[];
  onSend(content: string): Promise<void>;
}

export function ConsoleWindow({ agentStatus, messages, tasks, onSend }: ConsoleWindowProps) {
  return (
    <main className="console-window" aria-label="AuraBot console window">
      <header className="console-header">
        <div>
          <h1>AuraBot Console</h1>
          <p>Chat here when the desktop companion needs a full thread.</p>
        </div>
        <AgentStatusBar status={agentStatus} />
      </header>
      <TaskStatusOverlay tasks={tasks} />
      <section className="console-chat" aria-label="Main conversation">
        <MessageList messages={messages} />
        <Composer onSend={onSend} />
      </section>
    </main>
  );
}
