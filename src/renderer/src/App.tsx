import { useEffect } from "react";
import { useAuraBotStore } from "./app/store";
import { ConsoleWindow } from "./features/console/ConsoleWindow";
import { DesktopPet } from "./features/pet/DesktopPet";

export function App() {
  const loadInitialState = useAuraBotStore((state) => state.loadInitialState);
  const messages = useAuraBotStore((state) => state.messages);
  const tasks = useAuraBotStore((state) => state.tasks);
  const agentStatus = useAuraBotStore((state) => state.agentStatus);
  const petSettings = useAuraBotStore((state) => state.petSettings);
  const petActivity = useAuraBotStore((state) => state.petActivity);
  const petCursorPosition = useAuraBotStore((state) => state.petCursorPosition);
  const memoryDebugSnapshot = useAuraBotStore((state) => state.memoryDebugSnapshot);
  const debugPetOverlay = useAuraBotStore((state) => state.debugPetOverlay);
  const sendMessage = useAuraBotStore((state) => state.sendMessage);
  const updatePetSettings = useAuraBotStore((state) => state.updatePetSettings);
  const loadMemoryDebugSnapshot = useAuraBotStore((state) => state.loadMemoryDebugSnapshot);
  const setPetMousePassthrough = useAuraBotStore((state) => state.setPetMousePassthrough);
  const movePetWindowBy = useAuraBotStore((state) => state.movePetWindowBy);
  const showPetNotice = useAuraBotStore((state) => state.showPetNotice);
  const clearPetActivity = useAuraBotStore((state) => state.clearPetActivity);
  const openConsoleWindow = useAuraBotStore((state) => state.openConsoleWindow);
  const subscribeToPetEvents = useAuraBotStore((state) => state.subscribeToPetEvents);
  const surface = window.location.hash === "#console" ? "console" : "pet";

  useEffect(() => {
    void loadInitialState();
  }, [loadInitialState]);

  useEffect(() => subscribeToPetEvents(), [subscribeToPetEvents]);

  return (
    <div className={`app-shell app-shell-${surface}`}>
      {surface === "console" ? (
        <ConsoleWindow
          agentStatus={agentStatus}
          messages={messages}
          tasks={tasks}
          petSettings={petSettings}
          memoryDebugSnapshot={memoryDebugSnapshot}
          onUpdatePetSettings={updatePetSettings}
          onLoadMemoryDebugSnapshot={loadMemoryDebugSnapshot}
          onSend={sendMessage}
        />
      ) : (
        <DesktopPet
          agentStatus={agentStatus}
          petSettings={petSettings}
          petActivity={petActivity}
          petCursorPosition={petCursorPosition}
          debugPetOverlay={debugPetOverlay}
          onOpenConsole={openConsoleWindow}
          onSendVoiceInput={(content) => sendMessage(content, "pet_voice")}
          onSendTextInput={(content) => sendMessage(content, "pet_text")}
          onSetMousePassthrough={setPetMousePassthrough}
          onMovePetWindowBy={movePetWindowBy}
          onShowPetNotice={showPetNotice}
          onClearPetActivity={clearPetActivity}
        />
      )}
    </div>
  );
}
