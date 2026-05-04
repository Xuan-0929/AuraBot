import { useEffect } from "react";
import { useAuraBotStore } from "./app/store";
import { ConsoleWindow } from "./features/console/ConsoleWindow";
import { DesktopPet } from "./features/pet/DesktopPet";

export function App() {
  const loadInitialState = useAuraBotStore((state) => state.loadInitialState);
  const messages = useAuraBotStore((state) => state.messages);
  const tasks = useAuraBotStore((state) => state.tasks);
  const agentStatus = useAuraBotStore((state) => state.agentStatus);
  const sendMessage = useAuraBotStore((state) => state.sendMessage);
  const openConsoleWindow = useAuraBotStore((state) => state.openConsoleWindow);
  const surface = window.location.hash === "#console" ? "console" : "pet";

  useEffect(() => {
    void loadInitialState();
  }, [loadInitialState]);

  return (
    <div className={`app-shell app-shell-${surface}`}>
      {surface === "console" ? (
        <ConsoleWindow agentStatus={agentStatus} messages={messages} tasks={tasks} onSend={sendMessage} />
      ) : (
        <DesktopPet agentStatus={agentStatus} onOpenConsole={openConsoleWindow} />
      )}
    </div>
  );
}
