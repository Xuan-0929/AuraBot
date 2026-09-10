/** Composes the desktop shell and one Backend process around a controlled Profile repository.
 * AURABOT_USER_DATA_DIR selects the installation/data root independently of source code.
 * Main migrates configuration, serves owned model packages, serializes selection/application and
 * fences history receipts by their original Profile/generation. Pet alone owns realtime media;
 * Backend exclusively owns Context, Memory, task state and the active business lifecycle. */
import { app, BrowserWindow, ipcMain, Notification, dialog } from 'electron';
import { join, resolve, dirname } from 'node:path';
import { randomUUID } from 'node:crypto';
import { existsSync,readFileSync } from 'node:fs';
import { createLive2DModelRegistry } from '@core/live2d/modelRegistry';
import { ProfileRepository, findModelManifests } from '@core/profiles/profileRepository';
import { buildSoulPrompt } from '@core/profiles/soulPrompt';
import { registerProfileResourceScheme, installProfileResourceProtocol } from './profileResources';
import { avatarResourceUrl } from '@shared/contracts/avatar';
import type {ProfileConfig} from '@shared/contracts/profiles';
import type {ProfileRuntimeState,PetCommandResult} from '@shared/contracts/desktop';
import type {Live2DModelState} from '@shared/contracts/live2dModel';
import { IPC_CHANNELS as C } from '@shared/contracts/ipc';
import type { AgentStatus } from '@shared/contracts/chat';
import type { HistoryEntry, PetCommand } from '@shared/contracts/desktop';
import { DesktopConfigStore } from './desktopConfigStore';
import { ConversationHistory } from './conversationHistory';
import { BackendProcess, backendJson } from './backendProcess';
import { TaskStatusFeed } from './taskStatusFeed';
import { PetWindowStateStore } from './petWindowStateStore';
import { createPetWindow, openConsoleWindow, openSettingsWindow, applyPetSettings, broadcastLive2DModelState, broadcastChatMessageUpdated, broadcastPetActivity, broadcastAgentStatus, setPetMousePassthrough, movePetWindowBy, resizePetWindowCorner, restorePetWindowBounds } from './createWindow';

registerProfileResourceScheme();
if(process.env.AURABOT_USER_DATA_DIR)app.setPath('userData',resolve(process.env.AURABOT_USER_DATA_DIR));
const projectRoot=resolve(__dirname,'../../..');
let backend:BackendProcess|undefined;
let history:ConversationHistory|undefined;

let quitting=false;
let tasks:TaskStatusFeed|undefined;
const broadcast=(channel:string,value:unknown)=>BrowserWindow.getAllWindows().forEach(window=>window.webContents.send(channel,value));

async function bootstrap(){
 const root=resolve(process.env.AURABOT_USER_DATA_DIR??join(app.getPath('userData'),'desktop-v2'));
 const settingsPath=join(root,'settings.json');
 const legacySettings=existsSync(settingsPath)?JSON.parse(readFileSync(settingsPath,'utf8')):null;
 const config=new DesktopConfigStore(root,projectRoot);
 history=new ConversationHistory(root);
 const shared=config.snapshot().backend;
 const dataRoot=resolve(dirname(config.path),shared.app.data_dir);
 const profileStore=new ProfileRepository({profilesRoot:resolve(dataRoot,shared.main_agent.profile_dir),legacyDatabasePath:join(root,'profile.sqlite3'),legacySettingsPath:config.path});
 await profileStore.initialize();
 const bundledRoot=app.isPackaged?join(__dirname,'../renderer/assets/live2d'):join(projectRoot,'frontend/public/assets/live2d');
 const bundled=createLive2DModelRegistry({modelRootPath:bundledRoot});
 const seedModel=(id:string,modelId='hiyori')=>{const model=bundled.listAvailableModels().find(m=>m.id===modelId);if(model)return profileStore.importModel(id,join(bundledRoot,model.folderName),model.manifestFileName);return profileStore.get(id);};
 if(!legacySettings||legacySettings.version===1){const id=profileStore.selectedId();if(!profileStore.get(id).saved.adaptor.model){try{seedModel(id,legacySettings?.desktop?.currentModelId??'hiyori');profileStore.adoptMigratedModel(id);}catch{/* A failed optional initial visual import leaves Soul and all existing data intact. */}}}
 config.completeProfileMigration();
 let runtime:ProfileRuntimeState={selectedId:profileStore.selectedId(),activeId:null,revision:null,generation:0,online:false};
 let applied:ProfileConfig|null=profileStore.applied(runtime.selectedId);
 let switching=false;
 const modelState=():Live2DModelState=>{
   const snapshot=applied;
   const manifest=snapshot?.adaptor.model?.manifest;
   return {currentModelId:snapshot?.modelVersion??'',currentModelPath:manifest?avatarResourceUrl(snapshot!.profile.id,snapshot!.modelVersion,manifest):'',models:[],profileId:snapshot?.profile.id??runtime.selectedId,revision:snapshot?.revision??0,modelVersion:snapshot?.modelVersion??'',adaptor:snapshot?.adaptor};
 };
 installProfileResourceProtocol(profileStore);
 const bounds=new PetWindowStateStore(root);
 let pet:BrowserWindow;
 let petReady=false;
 let agentState:AgentStatus='idle';
 let realtimeError='';
 const pending=new Map<string,{resolve:(result?:PetCommandResult)=>void;reject:(error:Error)=>void;timer:ReturnType<typeof setTimeout>}>();
 const changed=()=>{if(config.online&&!quitting&&runtime.online)tasks?.start();else {tasks?.stop();if(!config.online){runtime={...runtime,activeId:null,online:false};broadcast(C.profilesChanged,runtime);}}broadcast(C.settingsChanged,config.status());};
 const profilesChanged=()=>broadcast(C.profilesChanged,runtime);
 backend=new BackendProcess(config,projectRoot,changed,()=>tasks?.stop());
 const sendPet=(command:PetCommand)=>{if(!petReady||pet.isDestroyed())throw new Error('桌宠尚未就绪');pet.webContents.send(C.petCommand,command);};
 const assertPet=(event:Electron.IpcMainInvokeEvent)=>{if(event.sender.id!==pet.webContents.id)throw new Error('此操作仅允许桌宠窗口执行');};
 // IPC is limited to renderer windows created by this application.
 const handle=(channel:string,listener:(event:Electron.IpcMainInvokeEvent,...args:any[])=>any)=>ipcMain.handle(channel,(event,...args)=>{
   if(!BrowserWindow.fromWebContents(event.sender)||event.senderFrame!==event.sender.mainFrame)throw new Error('未知窗口');
   return listener(event,...args);
 });
 handle(C.getInitialState,()=>{const profile=(applied??profileStore.get(runtime.selectedId).saved).profile;const page=history!.list(profile.id);return {profile,profileRuntime:runtime,mainSessionId:`main:${profile.id}`,messages:page.messages,historyCursor:page.nextCursor,agentStatus:agentState,chatError:realtimeError,petSettings:config.current().desktop.pet,live2dModelState:modelState(),notificationSettings:{systemNotificationsEnabled:config.current().desktop.systemNotificationsEnabled,followUpNotificationsEnabled:false,idleNudgeNotificationsEnabled:false,permissionState:Notification.isSupported()?'system_managed':'unsupported'},debugPetOverlay:false};});
 handle(C.openConsoleWindow,()=>{openConsoleWindow();});
 handle(C.openSettingsWindow,()=>{openSettingsWindow();});
 handle(C.getSettings,()=>config.status());
 handle(C.saveSettings,(_event,value,clear=[])=>{if(backend!.busy||switching)throw new Error('正在启动或应用配置，请完成后再保存。');if(!Array.isArray(clear)||!clear.every(k=>typeof k==='string'))throw new Error('无效密钥操作');const result=config.save(value,clear);changed();return result;});
 handle(C.applySettings,async(_event,force=false)=>{
   if(typeof force!=='boolean')throw new Error('无效应用请求');
   if(switching)throw new Error('正在应用 Profile，请稍候');
   const previous=config.status();
   const result=await backend!.apply(force);
   if(!result.busy){applyPetSettings(config.current().desktop.pet);if(previous.backendPending||!previous.online)await activate(runtime.selectedId,true,false,true);}
   return result;
 });
 handle(C.getHistory,(_event,before,profileId)=>{if(profileId!==undefined&&profileId!==runtime.selectedId)throw new Error('历史 Profile 已切换');return history!.list(runtime.selectedId,before);});
 handle(C.recordDelivery,(event,entry:HistoryEntry)=>{
   assertPet(event);if(!runtime.online||entry.profileId!==runtime.activeId||entry.generation!==runtime.generation)throw new Error('已撤销的 Profile 交付');const saved=history!.record(entry.profileId,entry);broadcastChatMessageUpdated(saved);
   if(entry.role==='assistant')broadcastPetActivity({id:entry.id,kind:'chat_reply',message:entry.content,createdAt:saved.createdAt});
   return saved;
 });
 handle(C.petReady,event=>{assertPet(event);petReady=true;});
 handle(C.petCommandResult,(event,result)=>{assertPet(event);const waiting=pending.get(result.id);if(!waiting)return;clearTimeout(waiting.timer);pending.delete(result.id);result.error?waiting.reject(new Error(result.error)):waiting.resolve(result);});
 handle(C.realtimeError,(event,error)=>{assertPet(event);if(typeof error!=='string'||error.length>1024)throw new Error('无效错误状态');realtimeError=error;broadcast(C.realtimeError,error);});
 handle(C.realtimeStatus,(event,state)=>{assertPet(event);if(!['idle','listening','thinking','typing','speaking'].includes(state))throw new Error('无效状态');agentState=state;broadcastAgentStatus(state);});
 handle(C.sendChatMessage,async(_event,input)=>{
   if(!config.online||!runtime.online||switching)throw new Error('Backend 未就绪，请先在设置中应用配置。');
   if(typeof input?.content!=='string'||!input.content.trim()||input.content.length>8000)throw new Error('请输入有效消息（最多 8000 字符）');
   const id=randomUUID();const owner=runtime.activeId!;const revision=runtime.revision;
   await new Promise<void>((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(id);reject(new Error('消息确认超时；未自动重发，请确认对话状态。'));},15000);pending.set(id,{resolve:()=>resolve(),reject,timer});try{sendPet({id,type:'text',text:input.content.trim()});}catch(error){clearTimeout(timer);pending.delete(id);reject(error);}});
   if(runtime.activeId!==owner||runtime.revision!==revision)throw new Error('消息所属 Profile 已切换');
   const message=history!.list(owner).messages.find(m=>m.id===id);
   if(!message)throw new Error('消息已提交，但历史尚未确认。');
   return {userMessage:message,agentStatus:agentState};
 });
 handle(C.taskSubscribe,(event,enabled)=>{
   if(event.sender.id===pet.webContents.id)throw new Error('Task 只在控制台展示');
   tasks?.stop();tasks=undefined;
   if(enabled){const sender=event.sender;tasks=new TaskStatusFeed(()=>runtime.activeId??runtime.selectedId,value=>{if(!sender.isDestroyed())sender.send(C.tasksChanged,value);});tasks.start();sender.once('destroyed',()=>{tasks?.stop();tasks=undefined;});}
 });

 // One serialized Profile transaction fences old renderer receipts before resource replacement.
 const petRequest=(type:PetCommand['type'])=>new Promise<PetCommandResult|undefined>((resolve,reject)=>{
   if(!petReady){resolve(undefined);return;}
   const id=randomUUID();const timer=setTimeout(()=>{pending.delete(id);reject(new Error('桌宠配置响应超时'));},type==='reconnect'?25000:15000);
   pending.set(id,{resolve,reject,timer});try{sendPet({id,type});}catch(error){clearTimeout(timer);pending.delete(id);reject(error);}
 });
 const activate=async(id:string,force=false,applyOnly=false,restore=false)=>{
   if(switching)throw new Error('正在切换 Profile，请稍候');
   if(applyOnly&&id!==runtime.selectedId)throw new Error('请先显式激活此 Profile');
   const detail=profileStore.get(id);
   const target=restore?(detail.applied??detail.saved):detail.saved;
   if(runtime.online&&runtime.activeId===id&&runtime.revision===target.revision&&applied?.modelVersion===target.modelVersion)return {runtime};
   if(!force&&runtime.online&&agentState!=='idle')return {runtime,busy:true};
   switching=true;const beforeRuntime={...runtime};let revoked=false;
   try {
     if(!config.online){profileStore.select(id);applied=detail.applied;runtime={selectedId:id,activeId:null,revision:applied?.revision??null,generation:runtime.generation+1,online:false,error:'已选中；Backend 离线，配置尚未激活'};broadcastLive2DModelState(modelState());profilesChanged();return {runtime};}
     if(!force){const check=await backendJson('/api/v1/main-agent/profile/activation-check');if(check.busy)return {runtime,busy:true};}
     await petRequest('disconnect');revoked=true;
     runtime={...runtime,activeId:null,online:false,generation:runtime.generation+1};
     tasks?.stop();
     const result=await backendJson('/api/v1/main-agent/profile/activate',{method:'POST',body:JSON.stringify({profile_id:id,revision:target.revision,soul:buildSoulPrompt(target.profile.soul),adaptor:target.adaptor,model_version:target.modelVersion,force}),signal:AbortSignal.timeout(40000)});
     profileStore.select(id);applied=target;
     runtime={selectedId:id,activeId:id,revision:target.revision,generation:result.generation,online:result.runtime_active===true,visualStatus:result.visual_status,capabilityNames:result.capability_names};
     realtimeError='';agentState='idle';broadcastAgentStatus(agentState);
     const savedBounds=bounds.getBounds(id);if(savedBounds)restorePetWindowBounds(pet,savedBounds);
     broadcastLive2DModelState(modelState());profilesChanged();tasks?.start();
     if(petReady){const ack=await petRequest('reconnect');if(ack?.visual){runtime={...runtime,visualStatus:ack.visual.available?(runtime.visualStatus==='degraded'?'degraded':'ready'):'unavailable',visualError:ack.visual.error};}}
     if(!restore||!detail.applied)profileStore.markApplied(id,target.revision);
     profilesChanged();return {runtime};
   }catch(error){const message=error instanceof Error?error.message:'Profile 激活失败';if(revoked){await petRequest('disconnect').catch(()=>undefined);runtime={...runtime,activeId:null,online:false,error:message};}else runtime={...beforeRuntime,error:message};profilesChanged();throw error;}
   finally{switching=false;}
 };
 handle(C.listProfiles,()=>({profiles:profileStore.list(),runtime}));
 handle(C.getProfile,(_event,id)=>profileStore.get(id));
 handle(C.createProfile,(_event,name)=>{let result=profileStore.create(name);try{result=seedModel(result.saved.profile.id);}catch{/* Created identity remains editable without an optional bundled model. */}profilesChanged();return result;});
 handle(C.saveProfile,(_event,id,input)=>{if(switching)throw new Error('正在应用 Profile，请稍候');const result=profileStore.save(id,input);profilesChanged();return result;});
 handle(C.activateProfile,(_event,id,force=false,applyOnly=false)=>{if(typeof force!=='boolean'||typeof applyOnly!=='boolean')throw new Error('无效激活请求');return activate(id,force,applyOnly);});
 handle(C.deleteProfile,async(_event,id)=>{
   profileStore.get(id);if(switching)throw new Error('正在切换 Profile');
   if(id===runtime.selectedId)throw new Error('请先切换到其他 Profile');
   if(!config.online)throw new Error('Backend 离线，无法确认任务和归档所有权；暂不能删除');
   switching=true;try{const guard=await backendJson('/api/v1/main-agent/profile/'+encodeURIComponent(id)+'/delete-check');
   const reasons:Record<string,string>={active_profile:'请先切换到其他 Profile',unfinished_tasks:'该 Profile 仍有未完成任务',unarchived_context:'该 Profile 仍有未归档的上下文',context_state_unavailable:'无法安全读取归档状态',activation_in_progress:'正在切换 Profile'};
   profileStore.delete(id,{allowed:guard.allowed===true,reason:reasons[guard.reason]??guard.reason??undefined});profilesChanged();}finally{switching=false;}
 });
 handle(C.importProfileModel,async(event,id)=>{
   profileStore.get(id);if(switching)throw new Error('正在应用 Profile');
   switching=true;try{
   const chosen=await dialog.showOpenDialog(BrowserWindow.fromWebContents(event.sender)!,{title:'导入完整 Live2D 模型文件夹',properties:['openDirectory']});
   if(chosen.canceled||!chosen.filePaths[0])return null;
   const folder=chosen.filePaths[0];const manifests=findModelManifests(folder);
   if(!manifests.length)throw new Error('文件夹内没有 .model3.json 模型入口');
   let index=0;
   if(manifests.length>1){const pick=await dialog.showMessageBox(BrowserWindow.fromWebContents(event.sender)!,{type:'question',title:'选择模型入口',message:'此文件夹包含多个模型，请选择入口',buttons:[...manifests,'取消'],cancelId:manifests.length,noLink:true});index=pick.response;if(index===manifests.length)return null;}
   const result=profileStore.importModel(id,folder,manifests[index]!);profilesChanged();return result;
   }finally{switching=false;}
 });
 handle(C.verifyProfileResources,(_event,id,version,files)=>{if(switching)throw new Error('正在应用 Profile');const result=profileStore.verifyResources(id,version,files);profilesChanged();return result;});
 handle(C.getRealtimeToken,async event=>{assertPet(event);if(!runtime.online||!runtime.activeId)throw new Error('Profile 尚未激活');const owner=runtime.activeId,generation=runtime.generation,revision=runtime.revision;const token=await backendJson('/api/v1/main-agent/realtime-token',{method:'POST',body:JSON.stringify({profileId:owner,revision})});if(owner!==runtime.activeId||generation!==runtime.generation||revision!==runtime.revision)throw new Error('Profile 已切换');runtime={...runtime,generation:token.generation};profilesChanged();return token;});
 handle(C.getLive2DModelState,()=>modelState());
 handle(C.setPetMousePassthrough,(event,enabled)=>{assertPet(event);if(typeof enabled==='boolean')setPetMousePassthrough(enabled);});
 const movement=(value:any)=>{if(!value||![value.x,value.y].every(v=>typeof v==='number'&&Number.isFinite(v)&&Math.abs(v)<=10000))throw new Error('无效窗口位移');return value;};
 handle(C.movePetWindowBy,(event,delta)=>{assertPet(event);movePetWindowBy(movement(delta));});
 handle(C.resizePetWindowCorner,(event,value)=>{assertPet(event);if(!['n','s','e','w','nw','ne','sw','se'].includes(value.direction))throw new Error('无效方向');resizePetWindowCorner(value.direction,movement(value.movement));});
 pet=createPetWindow({settings:config.current().desktop.pet,live2dModelState:modelState(),savedBounds:bounds.getBounds(runtime.selectedId),onBoundsChanged:value=>bounds.saveBounds(value,runtime.selectedId)});
 pet.webContents.on('render-process-gone',()=>{petReady=false;for(const item of pending.values()){clearTimeout(item.timer);item.reject(new Error('桌宠窗口已断开'));}pending.clear();});
 try{await backend.start();await activate(runtime.selectedId,true,false,true);}catch(error){config.error=error instanceof Error?error.message:'Backend 启动失败';changed();openSettingsWindow();}
}
app.whenReady().then(bootstrap).catch(error=>{dialog.showErrorBox('AuraBot 启动失败',error instanceof Error?error.message:'无法初始化应用数据。');app.quit();});
app.on('window-all-closed',()=>app.quit());
app.on('before-quit',event=>{
 if(quitting)return;event.preventDefault();quitting=true;tasks?.stop();
 void (async()=>{try{await backend?.stop();history?.close();app.quit();}catch(error){quitting=false;dialog.showErrorBox('AuraBot 尚未退出',error instanceof Error?error.message:'Backend 清理尚未完成，请稍后重试。');}})();
});
