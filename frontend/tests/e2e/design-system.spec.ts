/** Verifies the design system in real Electron windows with isolated application data.
 * Uses local delivered-message fixtures, screenshots both themes/minimum sizes, and exercises
 * sidebar focus, collapsed-field validation and desktop hover without external model requests. */
import {mkdtempSync, mkdirSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {_electron as electron, expect, test, type Page} from '@playwright/test';
const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const output = process.env.AURABOT_VISUAL_OUTPUT_DIR ?? '/private/tmp/aurabot-design-system';
test.setTimeout(120000);
test('renders compact chat/settings in both themes and keeps hover controls operable', async () => {
  mkdirSync(output, {recursive:true});
  const userDataDir = mkdtempSync(join(tmpdir(), 'aurabot-design-'));
  const app = await electron.launch({args:[join(appRoot,'out/main/index.js')], cwd:appRoot, env:{...process.env,AURABOT_USER_DATA_DIR:userDataDir}});
  try {
    const pet = await app.firstWindow();
    await expect.poll(() => pet.evaluate(async () => Boolean(window.aurabot) && (await window.aurabot.listProfiles()).runtime.online), {timeout:45000}).toBe(true);
    await pet.evaluate(async () => {
      const settings = await window.aurabot.getSettings();
      settings.config.desktop.pet.displayLanguage = 'zh';
      await window.aurabot.saveSettings(settings.config);await window.aurabot.applySettings();
      const {runtime} = await window.aurabot.listProfiles();
      for (const [index, item] of [
        {role:'user' as const,content:'帮我梳理一下今天的工作，先从设计系统开始。'},
        {role:'assistant' as const,content:'我们先把最重要的部分做好。\n\n统一颜色和操作层级，让对话保持自然，让配置更容易找到。'},
        {role:'user' as const,content:'配置页面能更紧凑一些吗？'},
        {role:'assistant' as const,content:'可以。常用设置直接展示，低频参数放进高级分组。\n\n保存和应用会保留明确的状态提示，你随时知道修改是否已经生效。'}
      ].entries()) await window.aurabot.recordDelivery({...item,id:`design-${index}`,profileId:runtime.activeId!,generation:runtime.generation});
    });
    const consolePromise = app.waitForEvent('window');await pet.evaluate(() => window.aurabot.openConsoleWindow());const chat = await consolePromise;
    await expect(chat.locator('.message-user')).toHaveCount(2);
    await expect(chat.locator('.message-list')).toContainText('你随时知道修改是否已经生效。');
    const settingsPromise = app.waitForEvent('window');await pet.evaluate(() => window.aurabot.openSettingsWindow());const settings = await settingsPromise;
    await expect(settings.getByRole('heading',{name:'模型',exact:true})).toBeVisible();
    async function size(page:Page,width:number,height:number) {
      const handle = await app.browserWindow(page);
      await handle.evaluate((window, dimensions) => window.setContentSize(dimensions.width, dimensions.height), {width,height});
      await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(width);
    }
    async function checkLayout(page:Page) {
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      const bar = page.locator('.settings-save-bar, .profile-save-bar, .console-composer').first();
      await expect(bar).toBeVisible();
      const bounds = await bar.boundingBox();const height=await page.evaluate(()=>innerHeight);
      expect(bounds!.y).toBeGreaterThanOrEqual(0);expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(height + 1);
    }
    for (const theme of ['light','dark'] as const) {
      await chat.emulateMedia({colorScheme:theme});await settings.emulateMedia({colorScheme:theme});
      await size(chat,1280,820);await size(settings,1280,820);
      await checkLayout(chat);await checkLayout(settings);
      const chatWindow = await app.browserWindow(chat);
      await chatWindow.evaluate(window => {
        window.webContents.send('tasks:changed',{tasks:[
          {id:'design-task-1',title:'整理设计系统',status:'running'},
          {id:'design-task-2',title:'准备本周工作笔记',status:'waiting'},
          {id:'design-task-3',title:'归档会议记录',status:'completed'}
        ]});
      });
      await expect(chat.locator('.task-list')).toContainText('整理设计系统');
      await chat.screenshot({animations:'disabled',path:join(output,`chat-${theme}.png`)});
      await settings.screenshot({animations:'disabled',path:join(output,`settings-${theme}.png`)});
      await size(chat,720,540);await size(settings,620,560);
      await checkLayout(chat);await checkLayout(settings);
      await expect(chat.getByRole('button',{name:'展开任务列表'})).toHaveAttribute('aria-expanded','false');
      await chat.getByRole('button',{name:'展开任务列表'}).click();
      await expect(chat.getByRole('dialog')).toBeVisible();
      await chat.keyboard.press('Escape');await expect(chat.getByRole('button',{name:'展开任务列表'})).toBeFocused();
      await chat.screenshot({animations:'disabled',path:join(output,`chat-${theme}-compact.png`)});
      await settings.screenshot({animations:'disabled',path:join(output,`settings-${theme}-compact.png`)});
    }
    await settings.locator('.settings-advanced').first().locator('summary').click();
    await settings.getByLabel('请求超时（秒）').first().fill('0');
    await settings.locator('.settings-advanced').first().locator('summary').click();
    await settings.getByRole('button',{name:'保存',exact:true}).click();
    await expect(settings.getByLabel('请求超时（秒）').first()).toBeVisible();
    await expect(settings.getByLabel('请求超时（秒）').first()).toBeFocused();
    await settings.getByLabel('请求超时（秒）').first().fill('60');
    await settings.getByRole('button',{name:'角色配置',exact:true}).click();
    await expect(settings.locator('.profile-list-row').first()).toBeVisible();
    await size(settings,1280,820);
    await settings.screenshot({animations:'disabled',path:join(output,'profiles-dark.png')});
    await settings.locator('.profile-list-row').first().getByRole('button',{name:'编辑',exact:true}).click();
    await expect(settings.locator('.profile-save-bar')).toBeVisible();await checkLayout(settings);
    await settings.screenshot({animations:'disabled',path:join(output,'profile-detail-dark.png')});
    await size(settings,620,560);await checkLayout(settings);
    await settings.screenshot({animations:'disabled',path:join(output,'profile-detail-dark-compact.png')});
    // Focus supplies a deterministic persistent hover-equivalent for screenshot capture.
    await pet.bringToFront();
    await pet.locator('.pet-action-console').focus();
    await expect(pet.locator('.pet-action-console')).toHaveCSS('opacity','1');
    await expect(pet.locator('.pet-action-console')).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
    await expect(pet.locator('.pet-vue-live2d-frame')).toHaveCSS('opacity','1');
    await expect(pet.locator('.pet-message-bubble')).toHaveCount(0,{timeout:20000});
    await pet.screenshot({animations:'disabled',path:join(output,'pet-controls.png'),omitBackground:true});
    await pet.locator('.pet-action-console').press('Enter');
    await expect(chat).toHaveURL(/#console$/);
    await pet.evaluate(()=>{(document.activeElement as HTMLElement)?.blur();});
    const petWindow = await app.browserWindow(pet);
    await petWindow.evaluate(w => { w.webContents.send('pet:cursor-position',{screenX:-1000,screenY:-1000,localX:-1000,localY:-1000}); });
    await expect(pet.locator('.pet-action-console')).toHaveCSS('opacity','0');
  } finally {await app.close();
   await expect.poll(async()=>{try{await fetch('http://127.0.0.1:6401/api/v1/readiness');return true;}catch{return false;}},{timeout:15000}).toBe(false);
   rmSync(userDataDir,{recursive:true,force:true});}
});
