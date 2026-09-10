/** Drives the real two-tab editor, SOUL files and production Cubism preview in a temporary data root.
 * Checks A/B isolation, stable natural labels, externally edited SOUL, preview teardown and restart.
 * No paid provider, microphone or real user directory participates in this test. */
import {mkdtempSync,rmSync,readFileSync,existsSync,cpSync,renameSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {_electron as electron,expect,test} from '@playwright/test';
import {t} from '../../src/i18n';
const appRoot=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
test.setTimeout(150000);
test('Profile workbench edits SOUL and actions without switching, releases previews and restores applied state',async()=>{
 const userDataDir=mkdtempSync(join(tmpdir(),'aurabot-profiles-e2e-'));
 let app=await electron.launch({args:[join(appRoot,'out/main/index.js')],cwd:appRoot,env:{...process.env,AURABOT_USER_DATA_DIR:userDataDir}});
 try{
  const pet=await app.firstWindow();
  await expect.poll(()=>pet.evaluate(async()=>Boolean(window.aurabot)&&(await window.aurabot.listProfiles()).runtime.online),{timeout:45000}).toBe(true);
  const a=(await pet.evaluate(()=>window.aurabot.listProfiles())).runtime.selectedId;
  const settingsPromise=app.waitForEvent('window');await pet.evaluate(()=>window.aurabot.openSettingsWindow());const settings=await settingsPromise;
  await settings.getByRole('button',{name:'角色配置',exact:true}).click();await settings.getByRole('button',{name:'创建',exact:true}).click();
  await settings.getByLabel('新角色名称').fill('Profile B');await settings.getByRole('button',{name:'创建角色',exact:true}).click();
  await expect(settings.getByRole('heading',{name:'角色配置 · Profile B',exact:true})).toBeVisible();
  const b=(await pet.evaluate(()=>window.aurabot.listProfiles())).profiles.find(p=>p.displayName==='Profile B')!.id;
  const soulFile=join(userDataDir,'profiles',b,'SOUL.md');
  await settings.getByLabel('SOUL',{exact:true}).fill('B 的私有人格，只属于 B。');await settings.getByRole('button',{name:'保存',exact:true}).click();
  await expect(settings.getByText('已保存，待应用',{exact:true})).toBeVisible();expect(readFileSync(soulFile,'utf8')).toBe('B 的私有人格，只属于 B。');expect(readFileSync(join(userDataDir,'profiles',a,'SOUL.md'),'utf8')).not.toBe(readFileSync(soulFile,'utf8'));
  expect((await pet.evaluate(()=>window.aurabot.listProfiles())).runtime.activeId).toBe(a);
  writeFileSync(soulFile,'外部编辑 B\n'+'自然人格。'.repeat(600)+'\nTAIL_RULE');await settings.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(settings.getByLabel('SOUL',{exact:true})).toHaveValue(readFileSync(soulFile,'utf8'));
  await settings.screenshot({path:'/private/tmp/aurabot-role-editor.png'});
  await settings.getByRole('tab',{name:'模型与动作',exact:true}).click();
  const source=join(userDataDir,'模型 source 中文');cpSync(join(appRoot,'public/assets/live2d/hiyori'),source,{recursive:true});renameSync(join(source,'Hiyori.model3.json'),join(source,'陪伴 model.model3.json'));
  const manifest=JSON.parse(readFileSync(join(source,'陪伴 model.model3.json'),'utf8'));manifest.FileReferences.Expressions=[{Name:'test_expression',File:'test.exp3.json'}];writeFileSync(join(source,'陪伴 model.model3.json'),JSON.stringify(manifest));writeFileSync(join(source,'test.exp3.json'),JSON.stringify({Type:'Live2D Expression',Parameters:[{Id:'ParamEyeLOpen',Value:0,Blend:'Overwrite'}]}));
  await app.evaluate(({dialog},folder)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[folder]});},source);
  await settings.getByRole('button',{name:'导入模型文件夹',exact:true}).click();await expect(settings.locator('.model-heading')).toContainText('陪伴 model');rmSync(source,{recursive:true,force:true});
  const anchor=settings.locator('.avatar-preview .live2d-vue-model-anchor');await expect(anchor).toHaveAttribute('data-avatar-state','ready',{timeout:25000});
  await expect.poll(()=>settings.evaluate(()=>{const canvas=document.querySelector('canvas');if(!canvas)return false;const gl=canvas.getContext('webgl2')??canvas.getContext('webgl');if(!gl)return false;const pixels=new Uint8Array(canvas.width*canvas.height*4);gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);return pixels.some((value,index)=>index%4===3&&value>0);}),{timeout:20000}).toBe(true);
  const first=settings.locator('.resource-row').first();await first.locator('.resource-choice').click();
  await settings.getByLabel('名称',{exact:true}).fill('测试动作');await settings.getByLabel('允许角色在对话中使用').check();await settings.getByLabel('用途说明').fill('用户通过实际预览确认的测试动作。');
  await settings.getByRole('tab',{name:'常驻',exact:true}).click();await settings.locator('[data-state=idle] summary').click();await settings.locator('[data-state=idle] input').first().check();await expect(settings.locator('[data-state=idle] input').first()).toBeEnabled();await settings.locator('[data-state=idle] input').nth(1).check();await expect(settings.locator('[data-state=idle] input').nth(1)).toBeEnabled();await settings.locator('[data-state=idle] input').last().check();await expect(settings.locator('[data-state=idle] input').last()).toBeEnabled();await settings.getByRole('tab',{name:'动作',exact:true}).click();
  await settings.getByRole('button',{name:'保存',exact:true}).click();await expect(settings.getByText('已保存，待应用',{exact:true})).toBeVisible();
  const saved=(await pet.evaluate(id=>window.aurabot.getProfile(id),b)).saved;const id=Object.keys(saved.adaptor.entries).find(id=>saved.adaptor.entries[id].agent_enabled)!;
  expect(saved.adaptor.schema_version).toBe(2);expect(saved.adaptor.states.idle).toHaveLength(3);expect(saved.adaptor.entries[id].display_name).toBe('测试动作');expect(saved.adaptor.entries[saved.adaptor.states.idle[0]!].playback.mode).toBe('loop');
  await settings.getByLabel('名称',{exact:true}).fill('Gentle motion');await settings.getByRole('button',{name:'保存',exact:true}).click();await expect(settings.getByText('已保存，待应用',{exact:true})).toBeVisible();
  expect((await pet.evaluate(id=>window.aurabot.getProfile(id),b)).saved.adaptor.entries[id].display_name).toBe('Gentle motion');
  await settings.getByRole('tab',{name:'表情',exact:true}).click();await settings.locator('.resource-row').first().locator('.resource-choice').click();
  await settings.getByRole('tab',{name:'常驻',exact:true}).click();await settings.getByRole('button',{name:'预览状态',exact:true}).first().click();
  const diagnostics=async()=>{const group=settings.locator('.profile-preview-diagnostics');if(!await group.evaluate(el=>(el as HTMLDetailsElement).open))await group.locator('summary').click();await group.getByRole('button',{name:'读取诊断'}).click();return JSON.parse(await group.locator('pre').innerText());};
  await expect.poll(async()=>(await diagnostics()).updates).toBeGreaterThan(2);expect((await diagnostics()).starts).toBeGreaterThan(0);await expect.poll(async()=>(await diagnostics()).residentStarts,{timeout:30000}).toBeGreaterThan(2);
  await settings.screenshot({path:'/private/tmp/aurabot-model-editor.png'});
  const settingsWindow=await app.browserWindow(settings);await settingsWindow.evaluate(w=>w.setContentSize(720,640));
  await expect.poll(()=>settings.evaluate(()=>innerWidth)).toBe(720);
  await settings.getByRole('button',{name:'预览状态',exact:true}).first().click();
  const previewBounds=await settings.locator('.avatar-preview').boundingBox(),barBounds=await settings.locator('.profile-save-bar').boundingBox();
  expect(previewBounds!.y).toBeGreaterThan(0);expect(previewBounds!.y+previewBounds!.height).toBeLessThan(barBounds!.y);
  expect(await settings.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await settings.screenshot({path:'/private/tmp/aurabot-model-editor-compact.png'});
  await settingsWindow.evaluate(w=>w.setContentSize(1280,820));

  for(let n=0;n<2;n++){await settings.getByRole('tab',{name:'角色',exact:true}).click();await expect(settings.locator('canvas')).toHaveCount(0);await settings.getByRole('tab',{name:'模型与动作',exact:true}).click();await expect(anchor).toHaveAttribute('data-avatar-state','ready',{timeout:15000});await settings.getByRole('tab',{name:'常驻',exact:true}).click();const counts=(await diagnostics()).instances;expect(counts.active).toBe(1);expect(counts.created-counts.destroyed).toBe(1);}
  expect((await pet.evaluate(()=>window.aurabot.listProfiles())).runtime.activeId).toBe(a);
  await settings.getByRole('tab',{name:'角色',exact:true}).click();await settings.getByRole('button',{name:'切换到此角色',exact:true}).click();
  await expect.poll(async()=>(await pet.evaluate(()=>window.aurabot.listProfiles())).runtime.activeId,{timeout:25000}).toBe(b);
  await expect.poll(async()=>(await pet.evaluate(id=>window.aurabot.getProfile(id),b)).applied?.adaptor.entries[id]?.agent_enabled).toBe(true);
  expect(existsSync(join(userDataDir,'profiles',a,'live2d/packages'))).toBe(true);expect(existsSync(join(userDataDir,'profiles',b,'live2d/packages'))).toBe(true);
  for(const language of ['en','ja','zh'] as const){
   await pet.evaluate(async language=>{const state=await window.aurabot.getSettings();state.config.desktop.pet.displayLanguage=language;await window.aurabot.saveSettings(state.config);await window.aurabot.applySettings();},language);
   await expect(settings.getByRole('heading',{name:t(language,'pTitle')+' · Profile B',exact:true})).toBeVisible();
   await expect(settings.getByRole('button',{name:t(language,'pOpenSoul'),exact:true})).toBeVisible();
   expect(await settings.getByLabel('SOUL',{exact:true}).inputValue()).toContain('TAIL_RULE');
   await settings.getByRole('tab',{name:t(language,'pModelTab'),exact:true}).click();await expect(anchor).toHaveAttribute('data-avatar-state','ready',{timeout:15000});
   await expect(settings.getByRole('button',{name:t(language,'pStop'),exact:true})).toBeVisible();
   if(language!=='zh')await settings.screenshot({path:'/private/tmp/aurabot-model-editor-'+language+'.png'});
   await settings.getByRole('tab',{name:t(language,'pRole'),exact:true}).click();
  }
  await settings.getByRole('button',{name:'返回角色列表',exact:true}).click();await expect(settings.locator('.profile-list-row').first()).toContainText('Profile B');await expect(settings.locator('.profile-list-row').first().getByRole('button',{name:'删除',exact:true})).toBeDisabled();
  await app.close();app=await electron.launch({args:[join(appRoot,'out/main/index.js')],cwd:appRoot,env:{...process.env,AURABOT_USER_DATA_DIR:userDataDir}});
  const restoredPet=await app.firstWindow();await expect.poll(()=>restoredPet.evaluate(async()=>{if(!window.aurabot)return null;const r=(await window.aurabot.listProfiles()).runtime;return r.online?r.activeId:null;}),{timeout:45000}).toBe(b);
  const restored=await restoredPet.evaluate(id=>window.aurabot.getProfile(id),b);expect(restored.saved.soulText).toContain('TAIL_RULE');expect(restored.applied?.soulText).toBe(restored.saved.soulText);expect(restored.applied?.adaptor.entries[id].agent_enabled).toBe(true);
 }finally{await app.close();await expect.poll(async()=>{try{await fetch('http://127.0.0.1:6401/api/v1/readiness');return true;}catch{return false;}},{timeout:15000}).toBe(false);rmSync(userDataDir,{recursive:true,force:true});}
});
