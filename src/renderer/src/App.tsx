import { useEffect } from "react";
import { useAuraBotStore } from "./app/store";
import { ConsoleWindow } from "./features/console/ConsoleWindow";
import { DesktopPet } from "./features/pet/DesktopPet";
import { SettingsWindow } from "./features/settings/SettingsWindow";

export function App() {
  const loadInitialState = useAuraBotStore((state) => state.loadInitialState);
  const messages = useAuraBotStore((state) => state.messages);
  const tasks = useAuraBotStore((state) => state.tasks);
  const agentStatus = useAuraBotStore((state) => state.agentStatus);
  const profile = useAuraBotStore((state) => state.profile);
  const petSettings = useAuraBotStore((state) => state.petSettings);
  const appConfig = useAuraBotStore((state) => state.appConfig);
  const modelHealth = useAuraBotStore((state) => state.modelHealth);
  const live2dModelState = useAuraBotStore((state) => state.live2dModelState);
  const notificationSettings = useAuraBotStore((state) => state.notificationSettings);
  const petActivity = useAuraBotStore((state) => state.petActivity);
  const petCursorPosition = useAuraBotStore((state) => state.petCursorPosition);
  const memoryDebugSnapshot = useAuraBotStore((state) => state.memoryDebugSnapshot);
  const debugPetOverlay = useAuraBotStore((state) => state.debugPetOverlay);
  const sendMessage = useAuraBotStore((state) => state.sendMessage);
  const cancelTask = useAuraBotStore((state) => state.cancelTask);
  const deleteTask = useAuraBotStore((state) => state.deleteTask);
  const updatePetSettings = useAuraBotStore((state) => state.updatePetSettings);
  const loadMemoryDebugSnapshot = useAuraBotStore((state) => state.loadMemoryDebugSnapshot);
  const setPetMousePassthrough = useAuraBotStore((state) => state.setPetMousePassthrough);
  const movePetWindowBy = useAuraBotStore((state) => state.movePetWindowBy);
  const showPetNotice = useAuraBotStore((state) => state.showPetNotice);
  const clearPetActivity = useAuraBotStore((state) => state.clearPetActivity);
  const openConsoleWindow = useAuraBotStore((state) => state.openConsoleWindow);
  const openSettingsWindow = useAuraBotStore((state) => state.openSettingsWindow);
  const openConfigFile = useAuraBotStore((state) => state.openConfigFile);
  const updateTaskRunnerSettings = useAuraBotStore((state) => state.updateTaskRunnerSettings);
  const updateModelProviderSettings = useAuraBotStore((state) => state.updateModelProviderSettings);
  const testModelConnection = useAuraBotStore((state) => state.testModelConnection);
  const updateProactiveSettings = useAuraBotStore((state) => state.updateProactiveSettings);
  const updatePersonaSettings = useAuraBotStore((state) => state.updatePersonaSettings);
  const setCurrentLive2DModel = useAuraBotStore((state) => state.setCurrentLive2DModel);
  const updateNotificationSettings = useAuraBotStore((state) => state.updateNotificationSettings);
  const subscribeToPetEvents = useAuraBotStore((state) => state.subscribeToPetEvents);
  const surface = window.location.hash === "#console" ? "console" : window.location.hash === "#settings" ? "settings" : "pet";

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
          appConfig={appConfig}
          memoryDebugSnapshot={memoryDebugSnapshot}
          onUpdatePetSettings={updatePetSettings}
          onOpenSettings={openSettingsWindow}
          onLoadMemoryDebugSnapshot={loadMemoryDebugSnapshot}
          onSend={sendMessage}
          onCancelTask={cancelTask}
          onDeleteTask={deleteTask}
        />
      ) : surface === "settings" ? (
        <SettingsWindow
          profile={profile}
          petSettings={petSettings}
          appConfig={appConfig}
          modelHealth={modelHealth}
          live2dModelState={live2dModelState}
          notificationSettings={notificationSettings}
          onUpdatePetSettings={updatePetSettings}
          onUpdateTaskRunnerSettings={updateTaskRunnerSettings}
          onUpdateModelProviderSettings={updateModelProviderSettings}
          onTestModelConnection={testModelConnection}
          onUpdateProactiveSettings={updateProactiveSettings}
          onUpdatePersonaSettings={updatePersonaSettings}
          onSetCurrentLive2DModel={setCurrentLive2DModel}
          onUpdateNotificationSettings={updateNotificationSettings}
          onOpenConfigFile={openConfigFile}
        />
      ) : (
        <DesktopPet
          agentStatus={agentStatus}
          modelPath={live2dModelState?.currentModelPath}
          petSettings={petSettings}
          petActivity={petActivity}
          petCursorPosition={petCursorPosition}
          debugPetOverlay={debugPetOverlay}
          onOpenConsole={openConsoleWindow}
          onOpenSettings={openSettingsWindow}
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
