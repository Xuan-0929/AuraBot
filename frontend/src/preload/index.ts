/** Bridges resource preparation and live diagnostics through typed IPC; Main owns paths, capture
 * permission fixed at launch and bounded logging. Pet is the sole permitted source of renderer diagnostic facts. */
/** Exposes only typed desktop IPC; Pet delivery and command methods are checked by Main against its webContents.
 * SOUL opening carries only a Profile identity. Renderer has no filesystem or Memory API access. Every subscription has an explicit disposer.
 */
import { contextBridge, ipcRenderer } from 'electron';
import { IPC_CHANNELS, type AuraBotApi } from '@shared/contracts/ipc';
function subscribe<T>(channel:string,listener:(value:T)=>void):()=>void {
 const handler=(_event:Electron.IpcRendererEvent,value:T)=>listener(value);
 ipcRenderer.on(channel,handler);return ()=>ipcRenderer.removeListener(channel,handler);
}
const api:AuraBotApi={
 getLiveDebugState:()=>ipcRenderer.invoke(IPC_CHANNELS.getLiveDebugState),
 openLiveDebugWindow:()=>ipcRenderer.invoke(IPC_CHANNELS.openLiveDebugWindow),
 clearLiveDebugLogs:()=>ipcRenderer.invoke(IPC_CHANNELS.clearLiveDebugLogs),
 openLiveDebugLogDirectory:()=>ipcRenderer.invoke(IPC_CHANNELS.openLiveDebugLogDirectory),
 onLiveDebugChanged:listener=>subscribe(IPC_CHANNELS.liveDebugChanged,listener),
 getLiveDebugCapture:()=>ipcRenderer.invoke(IPC_CHANNELS.getLiveDebugCapture),
 onLiveDebugCaptureChanged:listener=>subscribe(IPC_CHANNELS.liveDebugCaptureChanged,listener),
 reportLiveDebugEvent:event=>ipcRenderer.invoke(IPC_CHANNELS.reportLiveDebugEvent,event),
 listProfiles:()=>ipcRenderer.invoke(IPC_CHANNELS.listProfiles),getProfile:id=>ipcRenderer.invoke(IPC_CHANNELS.getProfile,id),
 openProfileSoul:(id,reveal)=>ipcRenderer.invoke(IPC_CHANNELS.openProfileSoul,id,reveal),
 createProfile:(name,language)=>ipcRenderer.invoke(IPC_CHANNELS.createProfile,name,language),saveProfile:(id,input)=>ipcRenderer.invoke(IPC_CHANNELS.saveProfile,id,input),
 activateProfile:(id,force,applyOnly)=>ipcRenderer.invoke(IPC_CHANNELS.activateProfile,id,force,applyOnly),deleteProfile:id=>ipcRenderer.invoke(IPC_CHANNELS.deleteProfile,id),
 importProfileModel:id=>ipcRenderer.invoke(IPC_CHANNELS.importProfileModel,id),verifyProfileResources:(id,version,files)=>ipcRenderer.invoke(IPC_CHANNELS.verifyProfileResources,id,version,files),
 onProfilesChanged:listener=>subscribe(IPC_CHANNELS.profilesChanged,listener),getRealtimeToken:()=>ipcRenderer.invoke(IPC_CHANNELS.getRealtimeToken),
 getInitialState:()=>ipcRenderer.invoke(IPC_CHANNELS.getInitialState),
 openConsoleWindow:()=>ipcRenderer.invoke(IPC_CHANNELS.openConsoleWindow),
 openSettingsWindow:()=>ipcRenderer.invoke(IPC_CHANNELS.openSettingsWindow),
 getSpeechAssets:()=>ipcRenderer.invoke(IPC_CHANNELS.getSpeechAssets),
 prepareSpeechAssets:()=>ipcRenderer.invoke(IPC_CHANNELS.prepareSpeechAssets),
 cancelSpeechAssets:()=>ipcRenderer.invoke(IPC_CHANNELS.cancelSpeechAssets),
 onSpeechAssetsChanged:listener=>subscribe(IPC_CHANNELS.speechAssetsChanged,listener),
 getSettings:()=>ipcRenderer.invoke(IPC_CHANNELS.getSettings),
 saveSettings:(config, clearSecrets)=>ipcRenderer.invoke(IPC_CHANNELS.saveSettings, config, clearSecrets),
 applySettings:(force)=>ipcRenderer.invoke(IPC_CHANNELS.applySettings, force),
 getHistory:(before,profileId)=>ipcRenderer.invoke(IPC_CHANNELS.getHistory, before,profileId),
 recordDelivery:(entry)=>ipcRenderer.invoke(IPC_CHANNELS.recordDelivery, entry),
 sendChatMessage:(input)=>ipcRenderer.invoke(IPC_CHANNELS.sendChatMessage, input),
 petCommandResult:(result)=>ipcRenderer.invoke(IPC_CHANNELS.petCommandResult, result),
 petReady:()=>ipcRenderer.invoke(IPC_CHANNELS.petReady),
 reportRealtimeError:(error)=>ipcRenderer.invoke(IPC_CHANNELS.realtimeError,error),
 onRealtimeError:(listener)=>subscribe(IPC_CHANNELS.realtimeError,listener),
 reportRealtimeState:(state)=>ipcRenderer.invoke(IPC_CHANNELS.realtimeStatus, state),
 subscribeTasks:(enabled)=>ipcRenderer.invoke(IPC_CHANNELS.taskSubscribe, enabled),
 getLive2DModelState:()=>ipcRenderer.invoke(IPC_CHANNELS.getLive2DModelState),
 setPetMousePassthrough:(enabled)=>ipcRenderer.invoke(IPC_CHANNELS.setPetMousePassthrough, enabled),
 movePetWindowBy:(delta)=>ipcRenderer.invoke(IPC_CHANNELS.movePetWindowBy, delta),
 resizePetWindowCorner:(direction,movement)=>ipcRenderer.invoke(IPC_CHANNELS.resizePetWindowCorner,{direction,movement}),
 onSettingsChanged:(listener)=>subscribe(IPC_CHANNELS.settingsChanged,listener),
 onPetCommand:(listener)=>subscribe(IPC_CHANNELS.petCommand,listener),
 onTasksChanged:(listener)=>subscribe(IPC_CHANNELS.tasksChanged,listener),
 onPetSettingsChanged:(listener)=>subscribe(IPC_CHANNELS.petSettingsChanged,listener),
 onPetActivity:(listener)=>subscribe(IPC_CHANNELS.petActivity,listener),
 onLive2DModelStateChanged:(listener)=>subscribe(IPC_CHANNELS.live2dModelStateChanged,listener),
 onNotificationSettingsChanged:(listener)=>subscribe(IPC_CHANNELS.notificationSettingsChanged,listener),
 onPetCursorPosition:(listener)=>subscribe(IPC_CHANNELS.petCursorPosition,listener),
 onAgentStatusChanged:(listener)=>subscribe(IPC_CHANNELS.agentStatusChanged,listener),
 onChatMessageCreated:(listener)=>subscribe(IPC_CHANNELS.chatMessageCreated,listener),
 onChatMessageUpdated:(listener)=>subscribe(IPC_CHANNELS.chatMessageUpdated,listener),
};
contextBridge.exposeInMainWorld('aurabot',api);
