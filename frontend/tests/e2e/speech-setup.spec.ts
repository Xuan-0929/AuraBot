/** Exercises the real resource UI, pinned upstream downloads and local ONNX voice startup.
 * Uses isolated app data and a silent fake microphone; no cloud provider or real device is claimed.
 * Opt in with AURABOT_SPEECH_DOWNLOAD_SMOKE=1 because this test downloads official model assets. */
import {mkdtempSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {_electron as electron,expect,test} from '@playwright/test';
test.setTimeout(240000);
test('prepares resources in settings, applies and toggles voice with real local models',async()=>{
 test.skip(process.env.AURABOT_SPEECH_DOWNLOAD_SMOKE!=='1','Explicit network smoke only');
 const root=mkdtempSync(join(tmpdir(),'aurabot-speech-e2e-'));
 const wav=Buffer.alloc(44+32000);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(16000,24);wav.writeUInt32LE(32000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(32000,40);const audio=join(root,'silence.wav');writeFileSync(audio,wav);
 let app=await electron.launch({args:[resolve('out/main/index.js'),'--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream',`--use-file-for-fake-audio-capture=${audio}`],cwd:process.cwd(),env:{...process.env,AURABOT_USER_DATA_DIR:root}});
 try{
  const pet=await app.firstWindow();
  await expect.poll(()=>pet.evaluate(async()=>Boolean(window.aurabot)&&(await window.aurabot.listProfiles()).runtime.online),{timeout:45000}).toBe(true);
  await pet.evaluate(async()=>{const {config}=await window.aurabot.getSettings();config.backend.chat_model={api_key:'unused-silent-smoke',base_url:'http://127.0.0.1:1/v1',model_name:'unused',timeout_seconds:60};config.backend.main_agent.speech.api_key='unused-silent-smoke';await window.aurabot.saveSettings(config);});
  const opened=app.waitForEvent('window');await pet.evaluate(()=>window.aurabot.openSettingsWindow());const settings=await opened;
  await settings.getByRole('button',{name:'实时语音',exact:true}).click();
  await settings.getByRole('button',{name:'一键准备',exact:true}).click();
  await expect(settings.locator('progress')).toBeVisible();
  await settings.screenshot({path:'/private/tmp/aurabot-speech-download.png'});
  await expect(settings.getByText('资源已准备，请保存并应用。',{exact:true})).toBeVisible({timeout:180000});
  await settings.getByLabel('启用语音能力').check();
  await settings.getByRole('button',{name:'保存',exact:true}).click();
  await settings.getByRole('button',{name:'应用',exact:true}).click();
  await expect(settings.locator('.settings-save-bar [role=status]')).toHaveText('已应用',{timeout:45000});
  await expect.poll(()=>pet.evaluate(async()=>(await window.aurabot.listProfiles()).runtime.online)).toBe(true);
  await settings.screenshot({path:'/private/tmp/aurabot-speech-ready.png'});
  const mic=pet.locator('.pet-action-voice');await mic.click({force:true});
  await expect(mic).toHaveAttribute('aria-pressed','true',{timeout:20000});
  await mic.click({force:true});await expect(mic).toHaveAttribute('aria-pressed','false');
  await mic.click({force:true});await expect(mic).toHaveAttribute('aria-pressed','true',{timeout:20000});
  await mic.click({force:true});
  const state=await settings.evaluate(()=>window.aurabot.getSpeechAssets());expect(state.assets.every(a=>a.ready)).toBe(true);
  const reused=await settings.evaluate(()=>window.aurabot.prepareSpeechAssets());expect(reused.phase).toBe('ready');
  await app.close();
  app=await electron.launch({args:[resolve('out/main/index.js')],cwd:process.cwd(),env:{...process.env,AURABOT_USER_DATA_DIR:root}});
  const restored=await app.firstWindow();
  await expect.poll(()=>restored.evaluate(async()=>Boolean(window.aurabot)&&(await window.aurabot.listProfiles()).runtime.online),{timeout:45000}).toBe(true);
  expect((await restored.evaluate(()=>window.aurabot.getSpeechAssets())).assets.every(a=>a.ready)).toBe(true);
  await expect(restored.locator('.pet-action-voice')).toHaveAttribute('aria-pressed','false');
 }catch(error){console.log('Speech smoke status',await app.windows()[0]?.evaluate(async()=>({runtime:(await window.aurabot.listProfiles()).runtime,error:(await window.aurabot.getSettings()).error})));throw error;}finally{await app.close();rmSync(root,{recursive:true,force:true});}
});
