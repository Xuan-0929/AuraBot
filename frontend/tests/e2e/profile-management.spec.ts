// Validates saved/applied status labels and the current combined settings navigation.
/** Drives real Settings Profile pages against Electron's repository and the Python lifecycle.
 * A/B data and imported bundled assets live exclusively in a temporary user-data root; no provider,
 * microphone or paid service is used. Preview checks exercise the real production Cubism player. */
import {mkdtempSync,rmSync,readFileSync,existsSync,cpSync,renameSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {_electron as electron,expect,test} from '@playwright/test';
const appRoot=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
test.setTimeout(120000);
test('Profile pages isolate B editing and preview from A, then explicitly activate B',async()=>{
 const userDataDir=mkdtempSync(join(tmpdir(),'aurabot-profiles-e2e-'));
 let app=await electron.launch({args:[join(appRoot,'out/main/index.js')],cwd:appRoot,env:{...process.env,AURABOT_USER_DATA_DIR:userDataDir}});
 try {
  const pet=await app.firstWindow();
  await expect.poll(()=>pet.evaluate(async()=>Boolean(window.aurabot)&&(await window.aurabot.listProfiles()).runtime.online),{timeout:45000}).toBe(true);
  const initial=await pet.evaluate(()=>window.aurabot.listProfiles());const a=initial.runtime.selectedId;
  const settingsPromise=app.waitForEvent('window');await pet.evaluate(()=>window.aurabot.openSettingsWindow());const settings=await settingsPromise;
  await settings.getByRole('button',{name:'角色配置',exact:true}).click();
  await settings.getByRole('button',{name:'创建',exact:true}).click();
  await settings.getByLabel('新角色名称').fill('Profile B');await settings.getByRole('button',{name:'创建角色',exact:true}).click();
  await expect(settings.getByText('正在编辑：Profile B',{exact:true})).toBeVisible();
  const b=(await pet.evaluate(()=>window.aurabot.listProfiles())).profiles.find(p=>p.displayName==='Profile B')!.id;expect(b).not.toBe(a);
  await settings.getByLabel('人格描述',{exact:true}).fill('B 的私有人格，只属于 B。');
  await settings.getByRole('button',{name:'保存',exact:true}).click();
  await expect(settings.getByText('已保存',{exact:false})).toBeVisible();
  expect((await pet.evaluate(()=>window.aurabot.listProfiles())).runtime.activeId).toBe(a);
  const aSoul=JSON.parse(readFileSync(join(userDataDir,'profiles',a,'soul.json'),'utf8'));
  const bSoul=JSON.parse(readFileSync(join(userDataDir,'profiles',b,'soul.json'),'utf8'));expect(bSoul.persona).toBe('B 的私有人格，只属于 B。');expect(aSoul.persona).not.toBe(bSoul.persona);
  const source=join(userDataDir,'模型 source 中文');cpSync(join(appRoot,'public/assets/live2d/hiyori'),source,{recursive:true});renameSync(join(source,'Hiyori.model3.json'),join(source,'陪伴 model.model3.json'));
  const manifest=JSON.parse(readFileSync(join(source,'陪伴 model.model3.json'),'utf8'));manifest.FileReferences.Expressions=[{Name:'test_expression',File:'test.exp3.json'}];writeFileSync(join(source,'陪伴 model.model3.json'),JSON.stringify(manifest));writeFileSync(join(source,'test.exp3.json'),JSON.stringify({Type:'Live2D Expression',Parameters:[{Id:'ParamEyeLOpen',Value:0,Blend:'Overwrite'}]}));
  await app.evaluate(({dialog},folder)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[folder]});},source);
  await settings.getByRole('button',{name:'导入模型文件夹',exact:true}).click();
  await expect(settings.getByText('model/陪伴 model.model3.json',{exact:true})).toBeVisible();
  rmSync(source,{recursive:true,force:true});
  expect((await pet.evaluate(()=>window.aurabot.listProfiles())).runtime.activeId).toBe(a);
  await settings.getByLabel('打开独立预览').check();
  await expect.poll(()=>settings.evaluate(()=>{const canvas=document.querySelector('canvas');if(!canvas)return false;const gl=canvas.getContext('webgl2')??canvas.getContext('webgl');if(!gl)return false;const pixels=new Uint8Array(canvas.width*canvas.height*4);gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);return pixels.some((value,index)=>index%4===3&&value>0);}),{timeout:20000}).toBe(true);
  await settings.getByRole('button',{name:'预览 / 验证',exact:true}).first().click();
  await expect(settings.getByText('motion · 已验证',{exact:true}).first()).toBeVisible();
  expect((await pet.evaluate(()=>window.aurabot.listProfiles())).runtime.activeId).toBe(a);
  const expression=settings.locator('.profile-resource').filter({hasText:'expression ·'});await expression.getByRole('button',{name:'预览 / 验证',exact:true}).click();await expect(expression.getByText('expression · 已验证',{exact:true})).toBeVisible();
  await settings.getByLabel('组合动作',{exact:true}).selectOption({index:1});await settings.getByLabel('组合表情',{exact:true}).selectOption('model/test.exp3.json');await settings.getByRole('button',{name:'待机 → 组合动作 → 待机',exact:true}).click();await expect(settings.locator('.avatar-preview .live2d-vue-model-anchor')).toHaveAttribute('data-avatar-state','ready');
  await settings.locator('.profile-preview-diagnostics summary').click();
  const diagnostics=async()=>{await settings.getByRole('button',{name:'读取播放诊断',exact:true}).click();return JSON.parse(await settings.locator('[aria-label="播放诊断"]').evaluateAll(nodes=>nodes[0]?.textContent??'null'));};
  await expect.poll(async()=>(await diagnostics())?.updates??0).toBeGreaterThan(2);
  expect((await diagnostics()).starts).toBeGreaterThan(0);
  for(let n=0;n<2;n++){
    await settings.getByLabel('打开独立预览').uncheck();await expect(settings.locator('canvas')).toHaveCount(0);
    await settings.getByLabel('打开独立预览').check();await expect.poll(async()=>(await diagnostics())?.updates??0).toBeGreaterThan(2);
    const counts=(await diagnostics()).instances;expect(counts.active).toBe(1);expect(counts.created-counts.destroyed).toBe(1);
  }
  await settings.getByRole('button',{name:'添加语义标记',exact:true}).first().click();
  await settings.getByLabel('稳定名称').fill('previewed_motion');await settings.getByLabel('稳定名称').blur();
  await settings.getByLabel('用途说明').fill('用户已预览的测试动作。');await settings.getByLabel('向 Agent 开放').check();
  await settings.getByRole('button',{name:'保存',exact:true}).click();
  await settings.getByLabel('打开独立预览').uncheck();await expect(settings.locator('canvas')).toHaveCount(0);
  await settings.getByRole('button',{name:'切换到此角色',exact:true}).click();
  await expect.poll(async()=>(await pet.evaluate(()=>window.aurabot.listProfiles())).runtime.activeId,{timeout:20000}).toBe(b);
  const state=await pet.evaluate(()=>window.aurabot.getInitialState());expect(state.profile.id).toBe(b);expect(state.live2dModelState.adaptor?.entries.previewed_motion.agent_enabled).toBe(true);
  expect(existsSync(join(userDataDir,'profiles',a,'live2d','packages'))).toBe(true);expect(existsSync(join(userDataDir,'profiles',b,'live2d','packages'))).toBe(true);
  await settings.getByRole('button',{name:'← 角色列表',exact:true}).click();
  const card=settings.locator('.profile-card').filter({has:settings.getByRole('heading',{name:'Profile B'})});await expect(card.getByRole('button',{name:'删除',exact:true})).toBeDisabled();
  const currentRow=settings.locator('.profile-list-row').first();
  await expect(currentRow).toContainText('Profile B');await expect(currentRow).toHaveAttribute('aria-current','true');await expect(currentRow).not.toContainText('视觉可用');
  await expect(currentRow).toHaveClass(/is-current/);
  expect(await currentRow.evaluate(element=>getComputedStyle(element).backgroundColor)).toBe('rgb(229, 238, 232)');
  await settings.screenshot({path:'/private/tmp/aurabot-role-list.png'});
  await expect.poll(async()=>(await pet.evaluate(id=>window.aurabot.getProfile(id),b)).applied?.adaptor.entries.previewed_motion?.agent_enabled).toBe(true);
  await app.close();
  app=await electron.launch({args:[join(appRoot,'out/main/index.js')],cwd:appRoot,env:{...process.env,AURABOT_USER_DATA_DIR:userDataDir}});
  const restoredPet=await app.firstWindow();
  await expect.poll(()=>restoredPet.evaluate(async()=>{if(!window.aurabot)return null;const runtime=(await window.aurabot.listProfiles()).runtime;return runtime.online?runtime.activeId:null;}),{timeout:45000}).toBe(b);
  const restored=await restoredPet.evaluate(id=>window.aurabot.getProfile(id),b);
  expect(restored.saved.profile.soul?.persona).toBe('B 的私有人格，只属于 B。');expect(restored.applied?.adaptor.entries.previewed_motion.agent_enabled).toBe(true);expect(restored.applied?.adaptor.model?.manifest).toBe('model/陪伴 model.model3.json');
  await expect.poll(()=>restoredPet.evaluate(()=>{const canvas=document.querySelector('canvas');if(!canvas)return false;const gl=canvas.getContext('webgl2')??canvas.getContext('webgl');if(!gl)return false;const pixels=new Uint8Array(canvas.width*canvas.height*4);gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);return pixels.some((value,index)=>index%4===3&&value>0);}),{timeout:20000}).toBe(true);

 }finally{await app.close();
   await expect.poll(async()=>{try{await fetch('http://127.0.0.1:6401/api/v1/readiness');return true;}catch{return false;}},{timeout:15000}).toBe(false);
   rmSync(userDataDir,{recursive:true,force:true});}
});
