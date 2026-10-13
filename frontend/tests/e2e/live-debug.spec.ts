/** Verifies the real Electron settings/debug lifecycle against its isolated Python backend.
 * Observational Pet IPC events exercise the production collector and JSONL writer without
 * microphone, cloud credentials or provider calls. Window/socket observations, unsaved drafts,
 * clear fencing and launch-scoped permission cross public desktop interfaces only. */
import { mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';

const appRoot=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
test.setTimeout(150000);
async function readiness(){return (await fetch('http://127.0.0.1:6401/api/v1/readiness')).json();}
async function stop(app:ElectronApplication){
 await app.close();
 await expect.poll(async()=>{try{await readiness();return true;}catch{return false;}},{timeout:15000}).toBe(false);
}
async function petWindow(app:ElectronApplication){
 await expect.poll(()=>app.windows().some(page=>page.url().endsWith('#pet'))).toBe(true);
 return app.windows().find(page=>page.url().endsWith('#pet'))!;
}
async function ready(pet:Page){
 await expect.poll(()=>pet.evaluate(async()=>Boolean(window.aurabot)&&(await window.aurabot.getSettings()).online&&(await window.aurabot.listProfiles()).runtime.online),{timeout:45000}).toBe(true);
 await expect(pet.locator('.pet-action-voice')).toHaveAttribute('aria-pressed','false');
}
async function debugWindows(app:ElectronApplication){
 return app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().filter(window=>window.webContents.getURL().endsWith('#live-debug')).map(window=>({id:window.id,size:window.getSize(),resizable:window.isResizable()})));
}
async function report(pet:Page,name:string,seq:number){
 await pet.evaluate(async({name,seq})=>{
  const capture=await window.aurabot.getLiveDebugCapture();const {runtime}=await window.aurabot.listProfiles();
  await window.aurabot.reportLiveDebugEvent({source:'pet',instanceId:'e2e-observational-pet',seq,captureId:capture.captureId,timestamp:Date.now(),
   profileId:runtime.activeId??undefined,generation:runtime.generation,configRevision:runtime.revision??0,
   stage:'capture',name,level:'info',details:{state:'off',text:name,active:false}});
 },{name,seq});
}

test('debug captures without its window and ordinary restart ignores legacy debug settings',async()=>{
 const userDataDir=mkdtempSync(join(tmpdir(),'aurabot-live-debug-e2e-'));
 const logPath=join(userDataDir,'logs','live-debug','live-debug.jsonl');
 const logs=()=>existsSync(logPath)?readFileSync(logPath,'utf8'):'';
 const sockets:Array<{page:Page;url:string}>=[];
 const launch=async(debug=true)=>{
  const value=await electron.launch({args:[join(appRoot,'out/main/index.js'),...(debug?['--aurabot-debug']:[])],cwd:appRoot,env:{...process.env,AURABOT_USER_DATA_DIR:userDataDir}});
  value.context().on('page',page=>page.on('websocket',socket=>sockets.push({page,url:socket.url()})));
  return value;
 };
 let app:ElectronApplication|undefined=await launch();
 try{
  let pet=await petWindow(app);await ready(pet);
  const initial=await readiness();expect(initial.ready).toBe(true);
  const settingsOpened=app.waitForEvent('window');await pet.evaluate(()=>window.aurabot.openSettingsWindow());const settings=await settingsOpened;
  await settings.getByRole('button',{name:'实时语音',exact:true}).click();
  const model=settings.getByLabel('模型名称',{exact:true}).first();await model.fill('unsaved-debug-draft');
  await settings.getByRole('button',{name:'桌宠状态',exact:true}).click();
  const socketsBefore=sockets.length;
  await expect.poll(()=>debugWindows(app!)).toHaveLength(1);let debug=app.windows().find(page=>page.url().endsWith('#live-debug'))!;
  await expect(settings.locator('[data-debug-settings]')).toBeVisible();
  await expect(settings.locator('[name=live-debug-enabled]')).toHaveCount(0);
  await expect(debug).toHaveURL(/#live-debug$/);await expect(debug.getByRole('heading',{name:'Live 语音 Debug',exact:true})).toBeVisible();
  await expect.poll(()=>debugWindows(app!)).toEqual([{id:expect.any(Number),size:[1120,760],resizable:true}]);
  const firstId=(await debugWindows(app))[0]!.id;
  await settings.getByRole('button',{name:'打开 Debug 窗口',exact:true}).click();
  expect((await debugWindows(app)).map(window=>window.id)).toEqual([firstId]);
  await settings.getByRole('button',{name:'实时语音',exact:true}).click();await expect(model).toHaveValue('unsaved-debug-draft');
  await expect(settings.getByRole('button',{name:'保存',exact:true})).toBeEnabled();
  expect((await pet.evaluate(()=>window.aurabot.getSettings())).config.backend.chat_model.model_name).not.toBe('unsaved-debug-draft');
  expect((await readiness()).processId).toBe(initial.processId);expect((await readiness()).instanceId).toBe(initial.instanceId);
  await expect(pet.locator('.pet-action-voice')).toHaveAttribute('aria-pressed','false');
  expect(await debug.locator('canvas').count()).toBe(0);

  await debug.close();await expect.poll(()=>debugWindows(app!)).toEqual([]);
  expect((await pet.evaluate(()=>window.aurabot.getLiveDebugCapture())).enabled).toBe(true);
  await report(pet,'e2e.background-observation',1);
  await expect.poll(logs).toContain('e2e.background-observation');
  const reopened=app.waitForEvent('window');await pet.evaluate(()=>window.aurabot.openLiveDebugWindow());debug=await reopened;
  await expect(debug.locator('[data-debug-event]')).toContainText(['e2e.background-observation']);
  await debug.locator('[data-debug-event]').filter({hasText:'e2e.background-observation'}).click();
  expect(await debug.locator('.debug-pagination').evaluate(element=>element.getBoundingClientRect().bottom<=innerHeight)).toBe(true);
  expect(logs().trim().split('\n').every(line=>{const event=JSON.parse(line);return typeof event.captureId==='string'&&typeof event.name==='string';})).toBe(true);
  const oldCapture=(await pet.evaluate(()=>window.aurabot.getLiveDebugCapture())).captureId;
  await debug.getByRole('button',{name:'清空 Debug 日志',exact:true}).click();
  await expect.poll(async()=>(await pet.evaluate(()=>window.aurabot.getLiveDebugCapture())).captureId).not.toBe(oldCapture);
  await expect.poll(logs).not.toContain('e2e.background-observation');
  await expect(debug.locator('[data-debug-event]').filter({hasText:'e2e.background-observation'})).toHaveCount(0);
  await debug.close();const afterClear=app.waitForEvent('window');await pet.evaluate(()=>window.aurabot.openLiveDebugWindow());debug=await afterClear;
  await expect(debug.getByRole('heading',{name:'Live 语音 Debug',exact:true})).toBeVisible();
  expect((await pet.evaluate(()=>window.aurabot.getLiveDebugState())).events.some(event=>event.name==='e2e.background-observation')).toBe(false);
  await report(pet,'e2e.after-clear',2);await expect.poll(logs).toContain('e2e.after-clear');
  await expect(debug.locator('[data-debug-event]')).toContainText(['e2e.after-clear']);

  expect((await readiness()).processId).toBe(initial.processId);
  expect(sockets.slice(socketsBefore)).toEqual([]);
  await stop(app);app=undefined;
  const stoppedLog=logs();
  const speechLog=readFileSync(join(userDataDir,'speech-diagnostics.log'),'utf8');
  const legacy=JSON.parse(readFileSync(join(userDataDir,'settings.json'),'utf8'));legacy.desktop.liveDebugEnabled=true;
  writeFileSync(join(userDataDir,'settings.json'),JSON.stringify(legacy));
  app=await launch(false);pet=await petWindow(app);await ready(pet);
  expect(await debugWindows(app)).toEqual([]);
  expect((await pet.evaluate(()=>window.aurabot.getSettings())).launchOptions).toEqual({debug:false,lockMemory:false});
  expect((await pet.evaluate(()=>window.aurabot.getLiveDebugCapture())).enabled).toBe(false);
  for(const action of ['openLiveDebugWindow','clearLiveDebugLogs','openLiveDebugLogDirectory'] as const){
   await expect(pet.evaluate(async action=>{await window.aurabot[action]();},action)).rejects.toThrow('debug_mode_required');
  }
  expect(logs()).toBe(stoppedLog);
  expect(readFileSync(join(userDataDir,'speech-diagnostics.log'),'utf8')).toBe(speechLog);
  const opened=app.waitForEvent('window');await pet.evaluate(()=>window.aurabot.openSettingsWindow());const normalSettings=await opened;
  await normalSettings.getByRole('button',{name:'桌宠状态',exact:true}).click();
  await expect(normalSettings.locator('[data-debug-settings]')).toHaveCount(0);
  expect((await readiness()).instanceId).not.toBe(initial.instanceId);
 }finally{if(app)await stop(app);rmSync(userDataDir,{recursive:true,force:true});}
});


test('locked memory and debug launch survive Backend restart',async()=>{
 const root=mkdtempSync(join(tmpdir(),'aurabot-locked-e2e-'));
 const app=await electron.launch({args:[join(appRoot,'out/main/index.js'),'--aurabot-lock-memory','--aurabot-debug'],cwd:appRoot,env:{...process.env,AURABOT_USER_DATA_DIR:root}});
 try{
  const pet=await petWindow(app);await ready(pet);
  const first=await readiness();
  expect(first.launchOptions).toEqual({debug:true,lockMemory:true});
  expect(first.memoryFixture).toBe('benchmark-memory-v1');
  const {runtime}=await pet.evaluate(()=>window.aurabot.listProfiles());
  expect(existsSync(join(root,'profiles',runtime.activeId!,'context.sqlite3'))).toBe(false);
  expect(existsSync(join(root,'profiles',runtime.activeId!,'memory','memory.sqlite3'))).toBe(false);
  await expect.poll(()=>debugWindows(app)).toHaveLength(1);
  expect(existsSync(join(root,'speech-diagnostics.log'))).toBe(true);
  const result=await pet.evaluate(async()=>{
   const state=await window.aurabot.getSettings();state.config.backend.chat_model.model_name='restart-mode-check';
   await window.aurabot.saveSettings(state.config);return window.aurabot.applySettings();
  });
  expect(result.busy).toBeUndefined();await ready(pet);
  const restarted=await readiness();expect(restarted.instanceId).not.toBe(first.instanceId);
  expect(restarted.launchOptions).toEqual({debug:true,lockMemory:true});
  expect((await pet.evaluate(()=>window.aurabot.getSettings())).launchOptions).toEqual({debug:true,lockMemory:true});
  await expect.poll(()=>readFileSync(join(root,'logs/live-debug/live-debug.jsonl'),'utf8')).toContain('benchmark-memory-v1');
 }finally{await stop(app);rmSync(root,{recursive:true,force:true});}
});
