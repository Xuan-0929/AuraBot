/** Runs the real Electron windows, Python child and SQLite delivery path with isolated application data.
 * A local deterministic Chat Completions server checks transport integration without claiming real model,
 * Pi or microphone acceptance. Also verifies saved/applied revisions, no-op desktop application, busy
 * restart cancellation and port ownership; real provider/hardware smoke remains a separate manual check.
 */
import { mkdtempSync, rmSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:http';
import { _electron as electron, expect, type ElectronApplication, type Page, test } from '@playwright/test';
const appRoot=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
test.setTimeout(90000);
async function launch(userDataDir=mkdtempSync(join(tmpdir(),'aurabot-e2e-'))){
 const electronApp=await electron.launch({args:[join(appRoot,'out/main/index.js')],cwd:appRoot,env:{...process.env,AURABOT_USER_DATA_DIR:userDataDir}});
 const pet=await electronApp.firstWindow();await expect.poll(()=>pet.evaluate(()=>Boolean(window.aurabot))).toBe(true);
 return {electronApp,pet,userDataDir};
}
async function ready(pet:Page){try{await expect.poll(()=>pet.evaluate(async()=>(await window.aurabot.getSettings()).online&&(await window.aurabot.listProfiles()).runtime.online),{timeout:40000}).toBe(true);}catch(error){console.log('STARTUP_DIAGNOSTIC',await pet.evaluate(async()=>{const settings=await window.aurabot.getSettings();return {backend:{online:settings.online,error:settings.error},runtime:(await window.aurabot.listProfiles()).runtime};}));throw error;}}
async function status(){return (await fetch('http://127.0.0.1:6401/api/v1/readiness')).json();}
async function close(app:ElectronApplication){await app.close();await expect.poll(async()=>{try{await status();return true;}catch{return false;}},{timeout:10000}).toBe(false);}

test('fresh desktop renders Hiyori, exposes unavailable models and applies saved config exactly once',async()=>{
 const {electronApp,pet,userDataDir}=await launch();
 try{
  await ready(pet);
  await expect.poll(()=>pet.evaluate(()=>{
    const canvas=document.querySelector('canvas');if(!canvas)return false;
    const gl=canvas.getContext('webgl2')??canvas.getContext('webgl');if(!gl)return false;
    const pixels=new Uint8Array(canvas.width*canvas.height*4);gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
    return pixels.some((value,index)=>index%4===3&&value>0);
  }),{timeout:20000}).toBe(true);
  await pet.screenshot({path:'/private/tmp/aurabot-pet-verified.png'});
  const initial=await status();expect(initial.capabilities).toEqual({text:false,voice:false,tasks:false});
  const settingsPromise=electronApp.waitForEvent('window');await pet.evaluate(()=>window.aurabot.openSettingsWindow());const settings=await settingsPromise;
  await expect(settings.getByRole('button',{name:'保存',exact:true})).toBeDisabled();
  await settings.getByRole('button',{name:'桌宠',exact:true}).click();
  await settings.getByLabel('系统通知').uncheck();
  await expect(settings.getByRole('button',{name:'应用',exact:true})).toBeDisabled();
  await settings.getByRole('button',{name:'保存',exact:true}).click();
  await expect(settings.getByRole('status')).toHaveText('已保存，待应用');
  expect((await status()).processId).toBe(initial.processId);
  await settings.getByRole('button',{name:'应用',exact:true}).click();
  await expect(settings.getByRole('status')).toHaveText('配置已应用');
  expect((await status()).processId).toBe(initial.processId);
  await settings.getByRole('button',{name:'模型',exact:true}).click();
  await settings.getByLabel('模型名称',{exact:true}).first().fill('unconfigured-smoke');
  await settings.getByRole('button',{name:'保存',exact:true}).click();
  await expect(settings.getByRole('status')).toHaveText('已保存，待应用');
  await settings.getByRole('button',{name:'应用',exact:true}).click();
  await expect(settings.getByRole('status')).toHaveText('配置已应用',{timeout:40000});
  expect((await status()).processId).not.toBe(initial.processId);
  const consolePromise=electronApp.waitForEvent('window');await pet.evaluate(()=>window.aurabot.openConsoleWindow());const consolePage=await consolePromise;
  await expect(consolePage.getByRole('complementary',{name:'任务',exact:true})).toBeVisible();
  await consolePage.locator('textarea').fill('未配置模型时不应记录成功');await consolePage.locator('textarea').press('Enter');
  await expect(consolePage.getByRole('alert')).toContainText('模型尚未配置');
  expect((await pet.evaluate(()=>window.aurabot.getHistory())).messages).toEqual([]);
  await settings.getByRole('button',{name:'运行状态',exact:true}).click();
  await settings.screenshot({path:'/private/tmp/aurabot-settings-verified.png'});
 }finally{await close(electronApp);rmSync(userDataDir,{recursive:true,force:true});}
});

test('real Python text delivery persists only say_to_user and busy restart cancellation leaves work alive',async()=>{
 let release:(()=>void)|undefined;let requestCount=0;
 const model=createServer((request,response)=>{
  request.resume();request.on('end',()=>{
   requestCount++;response.writeHead(200,{'Content-Type':'text/event-stream'});
   release=()=>{response.write('data: '+JSON.stringify({choices:[{delta:{content:'private model reasoning'}}]})+'\n\n');
    response.end('data: '+JSON.stringify({choices:[{delta:{tool_calls:[{index:0,id:'smoke-call-'+requestCount,type:'function',function:{name:'say_to_user',arguments:JSON.stringify({text:'已完成文字交付。'})}}]},finish_reason:'tool_calls'}]})+'\n\ndata: [DONE]\n\n');};
  });
 });
 await new Promise<void>(resolve=>model.listen(0,'127.0.0.1',resolve));const port=(model.address() as {port:number}).port;
 let {electronApp,pet,userDataDir}=await launch();
 try{
  await ready(pet);
  await pet.evaluate(async(port)=>{const s=await window.aurabot.getSettings();s.config.backend.chat_model={base_url:`http://127.0.0.1:${port}/v1`,model_name:'local-smoke',api_key:'local-fixture-key',timeout_seconds:60};await window.aurabot.saveSettings(s.config);await window.aurabot.applySettings();},port);
  const before=await status();
  let sendError='';
  const sending=pet.evaluate(()=>window.aurabot.sendChatMessage({content:'你好'})).catch(error=>{sendError=String(error);throw error;});
  void sending.catch(()=>undefined);
  try{await expect.poll(()=>requestCount,{timeout:18000}).toBe(1);}catch(error){
    console.log('TEXT_DIAGNOSTIC',JSON.stringify({sendError,pet:await pet.locator('body').innerText(),history:await pet.evaluate(()=>window.aurabot.getHistory()),readiness:await status(),metrics:await(await fetch('http://127.0.0.1:6401/api/v1/metrics')).json()}));throw error;
  }
  await pet.evaluate(async()=>{const s=await window.aurabot.getSettings();s.config.backend.chat_model.timeout_seconds=61;await window.aurabot.saveSettings(s.config);});
  const settingsPromise=electronApp.waitForEvent('window');await pet.evaluate(()=>window.aurabot.openSettingsWindow());const settings=await settingsPromise;
  await settings.getByRole('button',{name:'应用',exact:true}).click();
  await expect(settings.getByRole('alertdialog')).toContainText('正在处理对话');
  await expect(settings.getByRole('button',{name:'取消',exact:true})).toBeFocused();
  await settings.getByRole('button',{name:'取消',exact:true}).click();
  expect((await status()).processId).toBe(before.processId);expect((await status()).ready).toBe(true);
  release!();await sending;
  await expect.poll(async()=>(await pet.evaluate(()=>window.aurabot.getHistory())).messages.map(m=>m.content),{timeout:15000}).toEqual(['你好','已完成文字交付。']);
  const second=pet.evaluate(()=>window.aurabot.sendChatMessage({content:'这轮会在应用时中断'}));
  void second.catch(()=>undefined);
  await expect.poll(()=>requestCount,{timeout:10000}).toBe(2);await second;
  await settings.getByRole('button',{name:'应用',exact:true}).click();
  await expect(settings.getByRole('alertdialog')).toBeVisible();
  await settings.getByRole('button',{name:'仍然应用',exact:true}).click();
  await expect(settings.getByRole('status')).toHaveText('配置已应用',{timeout:40000});
  expect((await status()).processId).not.toBe(before.processId);
  expect((await pet.evaluate(()=>window.aurabot.getHistory())).messages.map(m=>m.content)).toEqual(['你好','已完成文字交付。','这轮会在应用时中断']);
  const ids=(await pet.evaluate(()=>window.aurabot.getHistory())).messages.map(m=>m.id);
  await close(electronApp);
  ({electronApp,pet}=await launch(userDataDir));await ready(pet);
  expect((await pet.evaluate(()=>window.aurabot.getHistory())).messages.map(m=>m.id)).toEqual(ids);
 }finally{await close(electronApp);model.closeAllConnections();await new Promise<void>(resolve=>model.close(()=>resolve()));rmSync(userDataDir,{recursive:true,force:true});}
});

test('occupied backend port reports an error and never terminates the existing listener',async()=>{
 const listener=createServer((_req,res)=>res.end('unrelated listener'));
 await new Promise<void>(resolve=>listener.listen(6401,'127.0.0.1',resolve));
 const {electronApp,pet,userDataDir}=await launch();
 try{
  await expect.poll(()=>pet.evaluate(async()=>(await window.aurabot.getSettings()).error),{timeout:10000}).toContain('6401 已被占用');
  expect(await(await fetch('http://127.0.0.1:6401')).text()).toBe('unrelated listener');
 }finally{await electronApp.close();listener.closeAllConnections();await new Promise<void>(resolve=>listener.close(()=>resolve()));rmSync(userDataDir,{recursive:true,force:true});}
});

test('real Electron loads the packaged AudioWorklet and completes successive synthetic clauses',async()=>{
 const {electronApp,pet,userDataDir}=await launch();
 try{
  await ready(pet);
  await pet.locator('.pet-vue-live2d-frame').click({position:{x:150,y:200}});
  const asset=readdirSync(join(appRoot,'out/renderer/assets')).find(name=>/^audio-worklet-.*\.js$/.test(name))!;
  const result=await pet.evaluate(async(asset)=>{
    const context=new AudioContext();const source=context.createOscillator();
    try{
      await context.audioWorklet.addModule(new URL('./assets/'+asset,location.href));
      const mic=new AudioWorkletNode(context,'aurabot-mic-capture');
      const playback=new AudioWorkletNode(context,'aurabot-tts-playback',{outputChannelCount:[1]});
      const silent=context.createGain();silent.gain.value=0;silent.connect(context.destination);
      source.connect(mic);mic.connect(silent);playback.connect(silent);
      const frame=new Promise<number>((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('capture timeout')),5000);mic.port.onmessage=event=>{if(event.data.type==='frame'){clearTimeout(timer);resolve(event.data.samples.length);}};});
      await context.resume();source.start();const samples=await frame;
      const completed:string[]=[];
      for(const clauseId of ['first','second']){
        const done=new Promise<{clauseId:string;endContextTime:number}>((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('playback timeout')),5000);playback.port.onmessage=event=>{if(event.data.type==='clause-complete'){clearTimeout(timer);resolve(event.data);}};});
        playback.port.postMessage({type:'offer',clauseId});playback.port.postMessage({type:'append',samples:new Int16Array(2400)});playback.port.postMessage({type:'finish',clauseId});
        const event=await done;
        const deadline=Date.now()+5000;
        while((context.getOutputTimestamp().contextTime??0)<event.endContextTime){if(Date.now()>deadline)throw new Error('device output clock timeout');await new Promise(resolve=>setTimeout(resolve,20));}
        completed.push(event.clauseId);
      }
      return {samples,completed};
    }finally{source.disconnect();await context.close();}
  },asset);
  expect(result).toEqual({samples:320,completed:['first','second']});
 }finally{await close(electronApp);rmSync(userDataDir,{recursive:true,force:true});}
});
