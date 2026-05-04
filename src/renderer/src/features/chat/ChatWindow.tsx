import { AgentStatusBar } from "../status/AgentStatusBar";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { useAuraBotStore } from "../../app/store";

export function ChatWindow() {
  const messages = useAuraBotStore((state) => state.messages);
  const agentStatus = useAuraBotStore((state) => state.agentStatus);
  const sendMessage = useAuraBotStore((state) => state.sendMessage);

  return (
    <main className="chat-window">
      <AgentStatusBar status={agentStatus} />
      <MessageList messages={messages} />
      <Composer onSend={sendMessage} />
    </main>
  );
}
