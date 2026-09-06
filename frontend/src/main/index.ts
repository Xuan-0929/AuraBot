/** Composes the desktop shell, single Backend child, settings and delivered-history database.
 * Pet alone owns realtime media; Main forwards console input and accepts receipts only from Pet.
 * Backend owns business logic. This entry never instantiates legacy Chat, Memory or proactive services.
 */
import { app, BrowserWindow, ipcMain, Notification, dialog } from 'electron';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { ProfileStorage } from '@core/profiles/profileStorage';
import { createLive2DModelRegistry } from '@core/live2d/modelRegistry';
import { IPC_CHANNELS as C } from '@shared/contracts/ipc';
import type { AgentStatus } from '@shared/contracts/chat';
import type { HistoryEntry, PetCommand } from '@shared/contracts/desktop';
import { DesktopConfigStore } from './desktopConfigStore';
import { ConversationHistory } from './conversationHistory';
import { BackendProcess } from './backendProcess';
import { TaskStatusFeed } from './taskStatusFeed';
import { PetWindowStateStore } from './petWindowStateStore';
import { createPetWindow, openConsoleWindow, openSettingsWindow, applyPetSettings, broadcastLive2DModelState, broadcastChatMessageUpdated, broadcastPetActivity, broadcastAgentStatus, setPetMousePassthrough, movePetWindowBy, resizePetWindowCorner } from './createWindow';

if(process.env.AURABOT_USER_DATA_DIR)app.setPath('userData',process.env.AURABOT_USER_DATA_DIR);
const projectRoot=resolve(__dirname,'../../..');
let backend:BackendProcess|undefined;
let history:ConversationHistory|undefined;
let profiles:ProfileStorage|undefined;
let quitting=false;
let tasks:TaskStatusFeed|undefined;
const broadcast=(channel:string,value:unknown)=>BrowserWindow.getAllWindows().forEach(window=>window.webContents.send(channel,value));

async function bootstrap(){
 const root=process.env.AURABOT_USER_DATA_DIR??join(app.getPath('userData'),'desktop-v2');
 const config=new DesktopConfigStore(root,projectRoot);
 history=new ConversationHistory(root);
 const profileStore=new ProfileStorage(join(root,'profile.sqlite3'));
 profiles=profileStore;
 profileStore.updateActivePersonaSettings(config.current().persona);
 const registry=createLive2DModelRegistry({modelRootPath:app.isPackaged?join(__dirname,'../renderer/assets/live2d'):join(projectRoot,'frontend/public/assets/live2d')});
 const modelState=()=>{const model=registry.resolveCurrentModel(config.current().desktop.currentModelId);return {currentModelId:model?.id??'',currentModelPath:model?.manifestPath??'',models:registry.listAvailableModels()};};
 const bounds=new PetWindowStateStore(root);
 let pet:BrowserWindow;
 let petReady=false;
 let agentState:AgentStatus='idle';
 let realtimeError='';
 const pending=new Map<string,{resolve:()=>void;reject:(error:Error)=>void;timer:ReturnType<typeof setTimeout>}>();
 const changed=()=>{if(config.online&&!quitting)tasks?.start();else tasks?.stop();broadcast(C.settingsChanged,config.status());};
 backend=new BackendProcess(config,projectRoot,changed,()=>tasks?.stop());
 const sendPet=(command:PetCommand)=>{if(!petReady||pet.isDestroyed())throw new Error('桌宠尚未就绪');pet.webContents.send(C.petCommand,command);};
 const assertPet=(event:Electron.IpcMainInvokeEvent)=>{if(event.sender.id!==pet.webContents.id)throw new Error('此操作仅允许桌宠窗口执行');};
 // IPC is limited to renderer windows created by this application.
 const handle=(channel:string,listener:(event:Electron.IpcMainInvokeEvent,...args:any[])=>any)=>ipcMain.handle(channel,(event,...args)=>{
   if(!BrowserWindow.fromWebContents(event.sender)||event.senderFrame!==event.sender.mainFrame)throw new Error('未知窗口');
   return listener(event,...args);
 });
 handle(C.getInitialState,()=>{const profile=profileStore.getOrSeedActiveProfile();return {profile,mainSessionId:`main:${profile.id}`,messages:history!.list(profile.id).messages,agentStatus:agentState,chatError:realtimeError,petSettings:config.current().desktop.pet,live2dModelState:modelState(),notificationSettings:{systemNotificationsEnabled:config.current().desktop.systemNotificationsEnabled,followUpNotificationsEnabled:false,idleNudgeNotificationsEnabled:false,permissionState:Notification.isSupported()?'system_managed':'unsupported'},debugPetOverlay:false};});
 handle(C.openConsoleWindow,()=>{openConsoleWindow();});
 handle(C.openSettingsWindow,()=>{openSettingsWindow();});
 handle(C.getSettings,()=>config.status());
 handle(C.saveSettings,(_event,value,clear=[])=>{if(backend!.busy)throw new Error('正在启动或应用配置，请完成后再保存。');if(!Array.isArray(clear)||!clear.every(k=>typeof k==='string'))throw new Error('无效密钥操作');const result=config.save(value,clear);changed();return result;});
 handle(C.applySettings,async(_event,force=false)=>{
   if(typeof force!=='boolean')throw new Error('无效应用请求');
   const previous=config.status();
   const result=await backend!.apply(force);
   if(!result.busy){profileStore.updateActivePersonaSettings(config.current().persona);applyPetSettings(config.current().desktop.pet);broadcastLive2DModelState(modelState());if(previous.backendPending||!previous.online)sendPet({id:randomUUID(),type:'reconnect'});}
   return result;
 });
 handle(C.getHistory,(_event,before)=>history!.list(profileStore.getOrSeedActiveProfile().id,before));
 handle(C.recordDelivery,(event,entry:HistoryEntry)=>{
   assertPet(event);const saved=history!.record(profileStore.getOrSeedActiveProfile().id,entry);broadcastChatMessageUpdated(saved);
   if(entry.role==='assistant')broadcastPetActivity({id:entry.id,kind:'chat_reply',message:entry.content,createdAt:saved.createdAt});
   return saved;
 });
 handle(C.petReady,event=>{assertPet(event);petReady=true;});
 handle(C.petCommandResult,(event,result)=>{assertPet(event);const waiting=pending.get(result.id);if(!waiting)return;clearTimeout(waiting.timer);pending.delete(result.id);result.error?waiting.reject(new Error(result.error)):waiting.resolve();});
 handle(C.realtimeError,(event,error)=>{assertPet(event);if(typeof error!=='string'||error.length>1024)throw new Error('无效错误状态');realtimeError=error;broadcast(C.realtimeError,error);});
 handle(C.realtimeStatus,(event,state)=>{assertPet(event);if(!['idle','listening','thinking','typing','speaking'].includes(state))throw new Error('无效状态');agentState=state;broadcastAgentStatus(state);});
 handle(C.sendChatMessage,async(_event,input)=>{
   if(!config.online)throw new Error('Backend 未就绪，请先在设置中应用配置。');
   if(typeof input?.content!=='string'||!input.content.trim()||input.content.length>8000)throw new Error('请输入有效消息（最多 8000 字符）');
   const id=randomUUID();
   await new Promise<void>((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(id);reject(new Error('消息确认超时；未自动重发，请确认对话状态。'));},15000);pending.set(id,{resolve,reject,timer});try{sendPet({id,type:'text',text:input.content.trim()});}catch(error){clearTimeout(timer);pending.delete(id);reject(error);}});
   const message=history!.list(profileStore.getOrSeedActiveProfile().id).messages.find(m=>m.id===id);
   if(!message)throw new Error('消息已提交，但历史尚未确认。');
   return {userMessage:message,agentStatus:agentState};
 });
 handle(C.taskSubscribe,(event,enabled)=>{
   if(event.sender.id===pet.webContents.id)throw new Error('Task 只在控制台展示');
   tasks?.stop();tasks=undefined;
   if(enabled){const sender=event.sender;tasks=new TaskStatusFeed(()=>profileStore.getOrSeedActiveProfile().id,value=>{if(!sender.isDestroyed())sender.send(C.tasksChanged,value);});tasks.start();sender.once('destroyed',()=>{tasks?.stop();tasks=undefined;});}
 });
 handle(C.getLive2DModelState,()=>modelState());
 handle(C.setPetMousePassthrough,(event,enabled)=>{assertPet(event);if(typeof enabled==='boolean')setPetMousePassthrough(enabled);});
 const movement=(value:any)=>{if(!value||![value.x,value.y].every(v=>typeof v==='number'&&Number.isFinite(v)&&Math.abs(v)<=10000))throw new Error('无效窗口位移');return value;};
 handle(C.movePetWindowBy,(event,delta)=>{assertPet(event);movePetWindowBy(movement(delta));});
 handle(C.resizePetWindowCorner,(event,value)=>{assertPet(event);if(!['n','s','e','w','nw','ne','sw','se'].includes(value.direction))throw new Error('无效方向');resizePetWindowCorner(value.direction,movement(value.movement));});
 pet=createPetWindow({settings:config.current().desktop.pet,live2dModelState:modelState(),savedBounds:bounds.getBounds(),onBoundsChanged:value=>bounds.saveBounds(value)});
 pet.webContents.on('render-process-gone',()=>{petReady=false;for(const item of pending.values()){clearTimeout(item.timer);item.reject(new Error('桌宠窗口已断开'));}pending.clear();});
 try{await backend.start();if(petReady)sendPet({id:randomUUID(),type:'reconnect'});}catch(error){config.error=error instanceof Error?error.message:'Backend 启动失败';changed();openSettingsWindow();}
}
app.whenReady().then(bootstrap).catch(error=>{dialog.showErrorBox('AuraBot 启动失败',error instanceof Error?error.message:'无法初始化应用数据。');app.quit();});
app.on('window-all-closed',()=>app.quit());
app.on('before-quit',event=>{
 if(quitting)return;event.preventDefault();quitting=true;tasks?.stop();
 void (async()=>{try{await backend?.stop();history?.close();profiles?.close();app.quit();}catch(error){quitting=false;dialog.showErrorBox('AuraBot 尚未退出',error instanceof Error?error.message:'Backend 清理尚未完成，请稍后重试。');}})();
});
