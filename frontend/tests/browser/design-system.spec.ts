/** Checks real responsive chat/settings geometry in both themes without desktop processes.
 * Compiled Vue/CSS consumes the read-only IPC fixture; native Pet hover/navigation stays in app-launch.
 * Screenshots are optional artifacts and all pass/fail decisions use observable layout assertions.
 * Voice switching checks stable control geometry, draft retention and rapid transitions in real CSS.
 * Form bounds and help alignment keep fields and text clear of the scrolling edge at both viewport sizes. */
import { type Page } from '@playwright/test';
import { test, expect, capture } from './fixtures';

test('voice mode keeps its switch position and draft through animated and rapid changes', async ({ page }) => {
  await page.goto('/#settings');
  await page.evaluate(() => Object.assign(window.aurabot, {
    getSpeechAssets: async () => ({ phase: 'idle', assets: [], paths: {}, received: 0, total: 1 }),
    onSpeechAssetsChanged: () => () => {}
  }));
  await page.getByRole('button', { name: '实时语音', exact: true }).click();
  const toggle = page.getByRole('switch');
  const voice = page.locator('input[name="realtime-voice"]');
  async function checkVoiceLayout() {
    const layout = await page.evaluate(() => {
      const container = document.querySelector('.settings-page-container')!;
      const section = document.querySelector('.settings-page-section')!;
      const right = container.getBoundingClientRect().left + container.clientWidth;
      return {
        width: section.getBoundingClientRect().width,
        clearance: [...section.querySelectorAll('.settings-page-field, p, .settings-field-help button')].map(element => right - element.getBoundingClientRect().right),
        helpOffsets: [...section.querySelectorAll('.settings-field-help')].map(help => help.querySelector('button')!.getBoundingClientRect().left - help.previousElementSibling!.querySelector('input')!.getBoundingClientRect().left),
        overflow: container.scrollWidth - container.clientWidth,
      };
    });
    expect(layout.width).toBeLessThanOrEqual(800);
    expect(Math.min(...layout.clearance)).toBeGreaterThanOrEqual(23);
    expect(layout.helpOffsets.every(offset => Math.abs(offset) < 1)).toBe(true);
    expect(layout.overflow).toBe(0);
  }
  for (const width of [1280, 620]) {
    await page.setViewportSize({ width, height: 820 });
    await checkVoiceLayout();
    const before = await toggle.boundingBox();
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-checked', 'true');
    expect(await toggle.boundingBox()).toEqual(before);
    await expect(voice).toBeVisible();
    await voice.fill('retained-voice');
    await checkVoiceLayout();
    expect(await toggle.boundingBox()).toEqual(before);
    await toggle.press('Space');
    await expect(page.getByRole('heading', { name: '会话模型', exact: true })).toBeVisible();
    expect(await toggle.boundingBox()).toEqual(before);
  }
  // Change again before the outgoing panel finishes fading; only the final mode may remain.
  await toggle.evaluate((element: HTMLButtonElement) => { element.click(); setTimeout(() => element.click(), 40); });
  await expect(toggle).toHaveAttribute('aria-checked', 'false');
  await expect(page.locator('.voice-details')).toHaveCount(1);
  await expect(page.getByRole('heading', { name: '会话模型', exact: true })).toBeVisible();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await toggle.click();
  await expect(voice).toHaveValue('retained-voice');
  await expect(page.locator('.voice-details')).toHaveCSS('transition-duration', '0s');
});

test('chat, settings and profile footers stay usable at compact sizes in both themes', async ({ context }) => {
  const chat = await context.newPage();await chat.goto('/#console');
  const settings = await context.newPage();await settings.goto('/#settings');
  await expect(chat.locator('.message-user')).toHaveCount(2);
  await expect(chat.locator('.message-list')).toContainText('你随时知道修改是否已经生效。');
  await expect(settings.getByRole('heading',{name:'能力集成',exact:true})).toBeVisible();
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
