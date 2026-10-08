/** Checks real responsive chat/settings geometry in both themes without desktop processes.
 * Compiled Vue/CSS consumes the read-only IPC fixture; native Pet hover/navigation stays in app-launch.
 * Screenshots are optional artifacts and all pass/fail decisions use observable layout assertions. */
import { type Page } from '@playwright/test';
import { test, expect, capture } from './fixtures';

test('chat, settings and profile footers stay usable at compact sizes in both themes', async ({ context }) => {
  const chat = await context.newPage();await chat.goto('/#console');
  const settings = await context.newPage();await settings.goto('/#settings');
  await expect(chat.locator('.message-user')).toHaveCount(2);
  await expect(chat.locator('.message-list')).toContainText('你随时知道修改是否已经生效。');
  await expect(settings.getByRole('heading',{name:'模型接入',exact:true})).toBeVisible();
  const resize = (page:Page,width:number,height:number) => page.setViewportSize({width,height});
  async function checkLayout(page:Page) {
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const bar = page.locator('.settings-save-bar, .profile-save-bar, .console-composer').first();
    await expect(bar).toBeVisible();
    const bounds = await bar.boundingBox();const height=await page.evaluate(()=>innerHeight);
    expect(bounds!.y).toBeGreaterThanOrEqual(0);expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(height + 1);
  }
  for (const theme of ['light','dark'] as const) {
    await chat.emulateMedia({colorScheme:theme});await settings.emulateMedia({colorScheme:theme});
    await resize(chat,1280,820);await resize(settings,1280,820);
    await checkLayout(chat);await checkLayout(settings);
    await expect(chat.locator('.task-list')).toContainText('整理设计系统');
    await capture(chat,`chat-${theme}`);
    await capture(settings,`settings-${theme}`);
    await resize(chat,720,540);await resize(settings,620,560);
    await checkLayout(chat);await checkLayout(settings);
    await expect(chat.getByRole('button',{name:'展开任务列表'})).toHaveAttribute('aria-expanded','false');
    await chat.getByRole('button',{name:'展开任务列表'}).click();
    await expect(chat.getByRole('dialog')).toBeVisible();
    await chat.keyboard.press('Escape');await expect(chat.getByRole('button',{name:'展开任务列表'})).toBeFocused();
    await capture(chat,`chat-${theme}-compact`);
    await capture(settings,`settings-${theme}-compact`);
  }
  // Unlike jsdom's synthetic invalid event, Chromium must reveal and focus a collapsed invalid field.
  await settings.locator('.settings-advanced').first().locator('summary').click();
  await settings.getByLabel('请求超时（秒）').first().fill('0');
  await settings.locator('.settings-advanced').first().locator('summary').click();
  await settings.getByRole('button',{name:'保存',exact:true}).click();
  await expect(settings.getByLabel('请求超时（秒）').first()).toBeVisible();
  await expect(settings.getByLabel('请求超时（秒）').first()).toBeFocused();
  await settings.getByLabel('请求超时（秒）').first().fill('60');
  await settings.getByRole('button',{name:'角色配置',exact:true}).click();
  await expect(settings.locator('.profile-list-row').first()).toBeVisible();
  await resize(settings,1280,820);
  await capture(settings,'profiles-dark');
  await settings.locator('.profile-list-row').first().getByRole('button',{name:'编辑',exact:true}).click();
  await expect(settings.locator('.profile-save-bar')).toBeVisible();await checkLayout(settings);
  await capture(settings,'profile-detail-dark');
  await resize(settings,620,560);await checkLayout(settings);
  await capture(settings,'profile-detail-dark-compact');
});
