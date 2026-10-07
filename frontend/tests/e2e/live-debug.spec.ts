/** Verifies the real Electron settings/debug lifecycle against its isolated Python backend.
 * Observational Pet IPC events exercise the production collector and JSONL writer without
 * microphone, cloud credentials or provider calls. Measured layout, anchored reading, window/socket observations, unsaved drafts,
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
  await settings.getByRole('button',{name:'模型接入',exact:true}).click();
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
  await settings.getByRole('button',{name:'模型接入',exact:true}).click();await expect(model).toHaveValue('unsaved-debug-draft');
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
  await debug.screenshot({path:'/private/tmp/aurabot-live-debug.png'});
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


test('debug gives events real reading space and keeps history stable at all window sizes and languages',async()=>{
 const userDataDir=mkdtempSync(join(tmpdir(),'aurabot-live-debug-layout-'));
 const app=await electron.launch({args:[join(appRoot,'out/main/index.js'),'--aurabot-debug'],cwd:appRoot,env:{...process.env,AURABOT_USER_DATA_DIR:userDataDir}});
 try{
  const pet=await petWindow(app);await ready(pet);
  await expect.poll(()=>debugWindows(app)).toHaveLength(1);const page=app.windows().find(page=>page.url().endsWith('#live-debug'))!;
  await expect(page.locator('[data-live-debug]')).toBeVisible();
  let revision=100000;
  const makeEvent=(id:number)=>({id,source:'backend',seq:id,instanceId:'layout',captureId:'layout',timestamp:1700000000000+id,stage:'stt',name:`event-${id}`,level:'info',details:{reason:`reason-${id}`,text:`long transcript ${id} `+'完整文字 '.repeat(100)}});
  const state={connection:{profileId:'profile-with-a-very-long-identifier-'.repeat(4),connected:true,generation:12,configRevision:17},transport:{state:'active',updatedAt:1700000000000},input:{rms:0.005,threshold:0.01,vad:0.75},capture:{sampleRate:16000,echoCancellation:true,noiseSuppression:true},stt:{stablePrefix:'稳定文字 '.repeat(300),mutableSuffix:'可变文字 '.repeat(120)},eou:{probability:0.8,eouThreshold:0.9},model:{state:'running',invokeId:'invoke-'.repeat(50)},tts:{queuedClauses:3},playback:{text:'回复文字 '.repeat(300)},devicePlayback:{queuedMs:1250,started:true}};
  const feed=async(events:any[],reset=false)=>{
   await app.evaluate(({BrowserWindow},update)=>{BrowserWindow.getAllWindows().find(window=>window.webContents.getURL().endsWith('#live-debug'))!.webContents.send('live-debug:changed',update);},{revision:++revision,events,state,reset,enabled:true,captureId:'layout',dropped:0,logDirectory:'/tmp/layout',updatedAt:Date.now()});
  };
  const resize=async(width:number,height:number)=>{await app.evaluate(({BrowserWindow},size)=>BrowserWindow.getAllWindows().find(window=>window.webContents.getURL().endsWith('#live-debug'))!.setSize(size.width,size.height),{width,height});await expect.poll(()=>page.evaluate(()=>innerWidth)).toBe(width);};
  const fullyVisible=()=>page.locator('.event-list').evaluate(list=>{const bounds=list.getBoundingClientRect();return [...list.querySelectorAll('[data-debug-event]')].filter(row=>{const r=row.getBoundingClientRect();return r.top>=bounds.top&&r.bottom<=bounds.bottom;}).length;});
  const scrollTop=()=>page.locator('.event-list').evaluate(list=>list.scrollTop);
  const rows=()=>page.locator('[data-debug-event]').evaluateAll(items=>items.map(item=>item.getAttribute('data-event-key')));
  for(const language of ['zh','en','ja'] as const){
   await pet.evaluate(async language=>{const settings=await window.aurabot.getSettings();settings.config.desktop.pet.displayLanguage=language;await window.aurabot.saveSettings(settings.config);},language);
   await page.reload();await expect(page.locator('[role=tab]').nth(1)).toHaveText(language==='zh'?'对话文字':language==='ja'?'会話テキスト':'Conversation text');
   await resize(1120,760);await feed(Array.from({length:450},(_,i)=>makeEvent(i+1)),true);
   await expect(page.locator('[data-debug-event]')).toHaveCount(200);
   if(language==='zh')await expect.poll(async()=>page.locator('.event-list').evaluate(list=>Math.abs(list.scrollHeight-list.clientHeight-list.scrollTop))).toBeLessThan(2);
   await expect.poll(fullyVisible).toBeGreaterThanOrEqual(14);
   await expect(page.locator('.debug-pagination')).toBeInViewport();
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
   expect(await page.locator('[data-state-group]').evaluateAll(groups=>groups.every(group=>getComputedStyle(group).overflowY==='visible'))).toBe(true);
   await page.screenshot({path:`/private/tmp/live-debug-redesign-${language}.png`});
   await resize(800,600);await expect.poll(fullyVisible).toBeGreaterThanOrEqual(8);
   await expect(page.locator('.compact-tabs')).toBeVisible();
   await page.locator('[data-debug-event]').last().click();
   await expect(page.locator('[data-event-detail]')).toBeVisible();
   await page.locator('[role=tab]').nth(1).click();await expect(page.locator('[data-transcripts]')).toBeVisible();
   expect(await page.locator('.transcripts p').evaluateAll(items=>items.every(item=>getComputedStyle(item).overflowY==='visible'&&item.scrollHeight<=item.clientHeight+1))).toBe(true);
   await page.screenshot({path:`/private/tmp/live-debug-redesign-${language}-text.png`});
   await page.locator('.compact-tabs button').first().click();
   await page.screenshot({path:`/private/tmp/live-debug-redesign-${language}-small.png`});
   await resize(1360,900);await expect.poll(fullyVisible).toBeGreaterThanOrEqual(14);
   await page.screenshot({path:`/private/tmp/live-debug-redesign-${language}-large.png`});
  }
  await resize(1120,760);await feed(Array.from({length:450},(_,i)=>makeEvent(i+1)),true);
  await page.locator('.page-controls button').first().click();
  await page.locator('.event-list').evaluate(list=>{list.scrollTop=200;list.dispatchEvent(new Event('scroll'));});
  const before=await rows(),top=await scrollTop();
  await feed([makeEvent(451),makeEvent(452)]);
  await expect(page.locator('[data-new-events]')).toContainText('2');expect(await rows()).toEqual(before);expect(await scrollTop()).toBe(top);
  await feed([]);expect(await scrollTop()).toBe(top);
  await page.locator('[data-new-events]').click();await expect(page.locator('[data-debug-event]').last()).toContainText('event-452');
  await expect.poll(async()=>page.locator('.event-list').evaluate(list=>Math.abs(list.scrollHeight-list.clientHeight-list.scrollTop))).toBeLessThan(2);
  await page.locator('.event-list').evaluate(list=>{list.scrollTop-=100;list.dispatchEvent(new Event('scroll'));});
  await expect(page.locator('[name=debug-autoscroll]')).not.toBeChecked();
  const paused=await scrollTop();await feed([makeEvent(453)]);await expect(page.locator('[data-new-events]')).toContainText('1');expect(await scrollTop()).toBe(paused);
  await feed(Array.from({length:1001},(_,i)=>makeEvent(i+454)));
  await expect(page.locator('[data-history-expired]')).toBeVisible();await expect(page.locator('[data-debug-event]')).toHaveCount(200);
 }finally{await stop(app);rmSync(userDataDir,{recursive:true,force:true});}
});

for(const debug of [false,true])test(`locked memory launch preserves mode after Backend restart (debug=${debug})`,async()=>{
 const root=mkdtempSync(join(tmpdir(),'aurabot-locked-e2e-'));
 const app=await electron.launch({args:[join(appRoot,'out/main/index.js'),'--aurabot-lock-memory',...(debug?['--aurabot-debug']:[])],cwd:appRoot,env:{...process.env,AURABOT_USER_DATA_DIR:root}});
 try{
  const pet=await petWindow(app);await ready(pet);
  const first=await readiness();
  expect(first.launchOptions).toEqual({debug,lockMemory:true});
  expect(first.memoryFixture).toBe('benchmark-memory-v1');
  const {runtime}=await pet.evaluate(()=>window.aurabot.listProfiles());
  expect(existsSync(join(root,'profiles',runtime.activeId!,'context.sqlite3'))).toBe(false);
  expect(existsSync(join(root,'profiles',runtime.activeId!,'memory','memory.sqlite3'))).toBe(false);
  await expect.poll(()=>debugWindows(app)).toHaveLength(debug?1:0);
  expect(existsSync(join(root,'speech-diagnostics.log'))).toBe(debug);
  const result=await pet.evaluate(async()=>{
   const state=await window.aurabot.getSettings();state.config.backend.chat_model.model_name='restart-mode-check';
   await window.aurabot.saveSettings(state.config);return window.aurabot.applySettings();
  });
  expect(result.busy).toBeUndefined();await ready(pet);
  const restarted=await readiness();expect(restarted.instanceId).not.toBe(first.instanceId);
  expect(restarted.launchOptions).toEqual({debug,lockMemory:true});
  expect((await pet.evaluate(()=>window.aurabot.getSettings())).launchOptions).toEqual({debug,lockMemory:true});
  if(debug)await expect.poll(()=>readFileSync(join(root,'logs/live-debug/live-debug.jsonl'),'utf8')).toContain('benchmark-memory-v1');
  else expect(existsSync(join(root,'logs/live-debug'))).toBe(false);
 }finally{await stop(app);rmSync(root,{recursive:true,force:true});}
});
