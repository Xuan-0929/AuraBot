/** Defines typed IPC for desktop settings, visible history and Pet-owned realtime.
 * Electron validates callers and payloads; Task is read-only and Memory has no renderer surface.
 */
import type { AppInitialState, SendChatMessageInput, SendChatMessageResult, AgentStatus } from './chat';
import type { Live2DModelState } from './live2dModel';
import type { PetActivity } from './pet';
import type { Message } from '../schemas/message';
import type { PetSettings } from '../schemas/petSettings';
import type { Profile } from '../schemas/profile';
import type { DesktopConfig, SettingsState, ApplyResult, HistoryPage, HistoryEntry, TaskSummary, PetCommand } from './desktop';
export const IPC_CHANNELS = {
 getInitialState:'app:get-initial-state',openConsoleWindow:'window:open-console',openSettingsWindow:'window:open-settings',
 getSettings:'settings:get',saveSettings:'settings:save',applySettings:'settings:apply',settingsChanged:'settings:changed',
 getHistory:'history:list',recordDelivery:'history:delivered',sendChatMessage:'chat:send-message',
 petCommand:'pet:command',petCommandResult:'pet:command-result',petReady:'pet:ready',realtimeStatus:'pet:realtime-status',realtimeError:'pet:realtime-error',
 taskSubscribe:'tasks:subscribe',tasksChanged:'tasks:changed',getLive2DModelState:'live2d:get-model-state',
 setPetMousePassthrough:'pet:set-mouse-passthrough',movePetWindowBy:'pet:move-window-by',resizePetWindowCorner:'pet:resize-window-corner',
 petSettingsChanged:'pet:settings-changed',petActivity:'pet:activity',live2dModelStateChanged:'live2d:model-state-changed',
 notificationSettingsChanged:'notifications:settings-changed',petCursorPosition:'pet:cursor-position',agentStatusChanged:'agent:status-changed',chatMessageCreated:'chat:message-created',chatMessageUpdated:'chat:message-updated'
} as const;
export interface PetCursorPosition {
  screenX: number;
  screenY: number;
  localX: number;
  localY: number;
}

export type PetResizeDirection = "n" | "s" | "e" | "w" | "nw" | "ne" | "sw" | "se";
export type PetResizeCorner = Extract<PetResizeDirection, "nw" | "ne" | "sw" | "se">;

export interface AuraBotApi {
 getInitialState():Promise<AppInitialState>;
 openConsoleWindow():Promise<void>; openSettingsWindow():Promise<void>;
 getSettings():Promise<SettingsState>; saveSettings(config:DesktopConfig,clearSecrets?:string[]):Promise<SettingsState>;
 applySettings(force?:boolean):Promise<ApplyResult>; onSettingsChanged(listener:(value:SettingsState)=>void):()=>void;
 getHistory(before?:number):Promise<HistoryPage>; recordDelivery(entry:HistoryEntry):Promise<Message>;
 sendChatMessage(input:SendChatMessageInput):Promise<SendChatMessageResult>;
 onPetCommand(listener:(command:PetCommand)=>void):()=>void;
 petCommandResult(result:{id:string;error?:string}):Promise<void>; petReady():Promise<void>;
 reportRealtimeState(state:AgentStatus):Promise<void>;
 reportRealtimeError(error:string):Promise<void>; onRealtimeError(listener:(error:string)=>void):()=>void;
 subscribeTasks(enabled:boolean):Promise<void>; onTasksChanged(listener:(value:{tasks:TaskSummary[];error?:string})=>void):()=>void;
 getLive2DModelState():Promise<Live2DModelState>;
 setPetMousePassthrough(enabled:boolean):Promise<void>;
 movePetWindowBy(delta:{x:number;y:number}):Promise<void>;
 resizePetWindowCorner(direction:PetResizeDirection,movement:{x:number;y:number}):Promise<void>;
 onPetSettingsChanged(listener:(value:PetSettings)=>void):()=>void;
 onPetActivity(listener:(value:PetActivity)=>void):()=>void;
 onLive2DModelStateChanged(listener:(value:Live2DModelState)=>void):()=>void;
 onNotificationSettingsChanged(listener:(value:import('./notificationSettings').NotificationSettings)=>void):()=>void;
 onPetCursorPosition(listener:(value:PetCursorPosition)=>void):()=>void;
 onAgentStatusChanged(listener:(value:AgentStatus)=>void):()=>void;
 onChatMessageCreated(listener:(value:Message)=>void):()=>void;
 onChatMessageUpdated(listener:(value:Message)=>void):()=>void;
}
