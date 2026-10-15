// Validates saved/applied status labels and the current combined settings navigation.
/** Conversation fixtures are configured on the active Profile through real IPC.
 * Exercises the actual local Provider → Python SAY stream → Pet AvatarPlayer production chain.
 * Startup waits for the first applied Profile before saving settings. Settings locates roles by their displayed fixture names and verifies a bundled motion through the SDK
 * before exposing its user-selected semantic name; internal Profile IDs remain only in IPC assertions.
 * A controlled SSE server gates internal clauses, proving one gesture per SAY, repeatability across SAYs,
 * text-only history, and old-Profile receipt rejection. Every model/data copy is in a temporary root;
 * no paid Provider, microphone, speech service or private user directory is used.
 */
import { mkdtempSync, rmSync } from 'node:fs';
import { createServer, type ServerResponse } from 'node:http';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
let semantic = ''; // Runtime IDs are discovered after the user names the action.
const firstText = ['第一分句已经交付。', '第二分句保持同一次表演。'];
const secondText = ['新的调用可以重复动作。', '这一句仍属于第二次表演。'];
test.setTimeout(150_000);

function sse(response: ServerResponse, value: unknown) {
  response.write('data: ' + JSON.stringify(value) + '\n\n');
}
function toolDelta(argumentsText: string, callId?: string) {
  return { choices: [{ delta: { tool_calls: [{ index: 0,
    ...(callId ? { id: callId, type: 'function' } : {}),
    function: { ...(callId ? { name: 'say_to_user' } : {}), arguments: argumentsText },
  }] } }] };
}
async function ready(pet: Page) {
  await expect.poll(() => pet.evaluate(async () => {
    if (!window.aurabot || !(await window.aurabot.getSettings()).online) return false;
    const {runtime} = await window.aurabot.listProfiles();
    return runtime.online && Boolean(runtime.activeId && (await window.aurabot.getProfile(runtime.activeId)).applied);
  }), { timeout: 45_000 }).toBe(true);
}
async function history(pet: Page, profileId?: string) {
  return pet.evaluate(async id => (await window.aurabot.getHistory(undefined, id)).messages, profileId);
}
async function close(app: ElectronApplication) {
  await app.close();
  await expect.poll(async () => {
    try { await fetch('http://127.0.0.1:6401/api/v1/readiness'); return true; }
    catch { return false; }
  }, { timeout: 15_000 }).toBe(false);
}

test('verified Profile ability reaches the real Pet once per SAY and stale Profile receipts stay isolated', async () => {
  let requestCount = 0;
  let releaseFirstTail: (() => void) | undefined;
  const offeredMotionEnums: string[][] = [];
  const offeredExpressionEnums: string[][] = [];
  const fixtureErrors: string[] = [];
  const receivedSouls:string[]=[];
  const provider = createServer((request, response) => {
    let raw = '';
    request.setEncoding('utf8');
    request.on('data', chunk => { raw += chunk; });
    request.on('end', () => {
      try {
        const envelope = JSON.parse(raw);
        receivedSouls.push(JSON.stringify(envelope.messages));
        const say = envelope.tools?.find((tool: { function?: { name?: string } }) => tool.function?.name === 'say_to_user');
        offeredMotionEnums.push(say?.function.parameters.properties.avatar.properties.motion.enum ?? []);
        offeredExpressionEnums.push(say?.function.parameters.properties.avatar.properties.expression.enum ?? []);
        requestCount++;
        response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
        sse(response, { choices: [{ delta: { content: 'private fixture reasoning must never enter history' } }] });
        if (requestCount === 1) {
          // Avatar completes before text. The first clause must deliver while the tool JSON is still open.
          sse(response, toolDelta('{"avatar":{"motion":"' + semantic + '"},"text":"' + firstText[0], 'avatar-say-1'));
          releaseFirstTail = () => {
            if (response.destroyed || response.writableEnded) return;
            sse(response, toolDelta(firstText[1] + '"}'));
            response.end('data: [DONE]\n\n');
          };
        } else if (requestCount === 2) {
          sse(response, toolDelta(JSON.stringify({ avatar: { motion: semantic }, text: secondText.join('') }), 'avatar-say-2'));
          response.end('data: [DONE]\n\n');
        } else {
          fixtureErrors.push('Unexpected extra model invoke: ' + requestCount);
          response.end('data: [DONE]\n\n');
        }
      } catch (error) {
        fixtureErrors.push(String(error));
        if (!response.headersSent) response.writeHead(500);
        response.end();
      }
    });
  });
  await new Promise<void>(resolve => provider.listen(0, '127.0.0.1', resolve));
  const port = (provider.address() as { port: number }).port;
  const userDataDir = mkdtempSync(join(tmpdir(), 'aurabot-avatar-delivery-'));
  let app: ElectronApplication | undefined;
  try {
    app = await electron.launch({ args: [join(appRoot, 'out/main/index.js')], cwd: appRoot,
      env: { ...process.env, AURABOT_USER_DATA_DIR: userDataDir } });
    const pet = await app.firstWindow();
    await ready(pet);
    await pet.evaluate(async port => {
      const {runtime} = await window.aurabot.listProfiles();
      const {saved} = await window.aurabot.getProfile(runtime.selectedId);
      saved.voice.chat_model = { base_url: `http://127.0.0.1:${port}/v1`,
        model_name: 'local-avatar-fixture', api_key: 'local-fixture-key', timeout_seconds: 60 };
      await window.aurabot.saveProfile(runtime.selectedId,{...saved,expectedSoulFingerprint:saved.soulFingerprint});
      await window.aurabot.activateProfile(runtime.selectedId,false,true);
    }, port);
    await ready(pet);
    const initialProfiles = await pet.evaluate(() => window.aurabot.listProfiles());
    const a = initialProfiles.runtime.activeId!;
    const aName = initialProfiles.profiles.find(profile => profile.id === a)!.displayName;
    const settingsPromise = app.waitForEvent('window');
    await pet.evaluate(() => window.aurabot.openSettingsWindow());
    const settings = await settingsPromise;
    await settings.getByRole('button', { name: '角色配置', exact: true }).click();
    const aRow = settings.locator('.profile-list-row').filter({
      has: settings.getByRole('heading', { name: aName })
    });
    await aRow.getByRole('button', { name: '编辑', exact: true }).click();
    await settings.getByLabel('SOUL',{exact:true}).fill('Companion. '.repeat(250)+'TAIL_RULE_COMPLETE_SOUL');
    await settings.getByRole('tab',{name:'模型与动作',exact:true}).click();
    await expect(settings.locator('.avatar-preview .live2d-vue-model-anchor')).toHaveAttribute('data-avatar-state', 'ready', { timeout: 25_000 });
    const resource = settings.locator('.resource-row').first();
    const resourceFile = await resource.locator('.resource-choice').getAttribute('title');
    await resource.locator('.resource-choice').click();
    await settings.getByLabel('名称',{exact:true}).fill('已验证的测试动作');
    await settings.getByLabel('允许角色在对话中使用').check();
    await settings.getByLabel('用途说明').fill('通过真实 SDK 预览确认的测试资源。');
    await settings.locator('.profile-save-bar').getByRole('button', { name: '保存', exact: true }).click();
    await expect(settings.locator('.profile-save-bar')).toContainText('已保存');
    const configured=await pet.evaluate(id=>window.aurabot.getProfile(id),a);
    semantic=Object.keys(configured.saved.adaptor.entries).find(id=>configured.saved.adaptor.entries[id].agent_enabled)!;
    await settings.getByRole('tab',{name:'角色',exact:true}).click();
    await expect(settings.locator('.avatar-preview canvas')).toHaveCount(0);
    await settings.locator('.profile-save-bar').getByRole('button', { name: '应用', exact: true }).click();
    await expect.poll(async () => (await pet.evaluate(() => window.aurabot.listProfiles())).runtime.capabilityNames?.motions,
      { timeout: 25_000 }).toContain(semantic);
    await expect.poll(async()=> (await pet.evaluate(id=>window.aurabot.getProfile(id),a)).applied?.adaptor.entries[semantic]?.source.file,{timeout:25000}).toBe(resourceFile);
    const applied = await pet.evaluate(id => window.aurabot.getProfile(id), a);
    expect(applied.applied?.adaptor.entries[semantic].source.file).toBe(resourceFile);
    expect(applied.applied?.adaptor.validated_resources).toContain(resourceFile);
    const anchor = pet.locator('.live2d-vue-model-anchor');
    await expect(anchor).toHaveAttribute('data-avatar-state', 'ready');
    await expect(anchor).toHaveAttribute('data-motion-starts', '0');

    await pet.evaluate(() => window.aurabot.sendChatMessage({ content: '请执行第一次测试表演。' }));
    await expect.poll(() => requestCount, { timeout: 20_000 }).toBe(1);
    await expect(anchor).toHaveAttribute('data-motion-starts', '1', { timeout: 15_000 });
    await expect.poll(async () => (await history(pet, a)).map(message => message.content))
      .toEqual(['请执行第一次测试表演。', firstText[0]]);
    expect(receivedSouls[0]).toContain('TAIL_RULE_COMPLETE_SOUL');
    expect(offeredMotionEnums[0]).toEqual(['none', semantic]);
    // Hiyori has no expression files: the provider must only receive the application control value.
    expect(offeredExpressionEnums[0]).toEqual(['neutral']);
    expect(releaseFirstTail).toBeDefined();
    releaseFirstTail!();
    releaseFirstTail = undefined;
    await expect.poll(async () => (await history(pet, a)).map(message => message.content))
      .toEqual(['请执行第一次测试表演。', ...firstText]);
    await expect(anchor).toHaveAttribute('data-motion-starts', '1');

    await pet.evaluate(() => window.aurabot.sendChatMessage({ content: '请再次执行相同动作。' }));
    await expect.poll(() => requestCount, { timeout: 20_000 }).toBe(2);
    await expect(anchor).toHaveAttribute('data-motion-starts', '2', { timeout: 15_000 });
    const expectedA = ['请执行第一次测试表演。', ...firstText, '请再次执行相同动作。', ...secondText];
    await expect.poll(async () => (await history(pet, a)).map(message => message.content)).toEqual(expectedA);
    await expect(anchor).toHaveAttribute('data-motion-starts', '2');
    const aHistory = await history(pet, a);
    expect(aHistory.every(message => message.profileId === a)).toBe(true);
    expect(aHistory.filter(message => message.role === 'assistant').map(message => message.content)).toEqual([...firstText, ...secondText]);
    expect(JSON.stringify(aHistory)).not.toContain('private fixture reasoning');
    expect(JSON.stringify(aHistory)).not.toContain(semantic);
    expect(fixtureErrors).toEqual([]);

    const aScope = (await pet.evaluate(() => window.aurabot.listProfiles())).runtime;
    await settings.getByRole('button', { name: '返回角色列表', exact: true }).click();
    await settings.getByRole('button',{name:'创建',exact:true}).click();
  await settings.getByLabel('新角色名称').fill('Avatar delivery B');
    await settings.getByRole('button', { name: '创建角色', exact: true }).click();
    await settings.getByRole('button', { name: '切换到此角色', exact: true }).click();
    await expect.poll(async () => {
      const runtime = (await pet.evaluate(() => window.aurabot.listProfiles())).runtime;
      return runtime.online && Boolean(runtime.activeId) && runtime.activeId !== a;
    }, { timeout: 25_000 }).toBe(true);
    const bScope = (await pet.evaluate(() => window.aurabot.listProfiles())).runtime;
    expect(bScope.activeId).toBeTruthy();
    expect(bScope.online).toBe(true);
    const rejection = await pet.evaluate(async scope => {
      try {
        await window.aurabot.recordDelivery({ profileId: scope.activeId!, generation: scope.generation,
          id: 'late-old-profile-delivery', role: 'assistant', content: '迟到的 A 私有正文不得写入 B。' });
        return 'unexpectedly accepted';
      } catch (error) { return String(error); }
    }, aScope);
    expect(rejection).toContain('已撤销的 Profile 交付');
    expect(await history(pet, bScope.activeId!)).toEqual([]);
    await expect(history(pet,a)).rejects.toThrow('历史 Profile 已切换');
    await settings.getByRole('button',{name:'返回角色列表',exact:true}).click();
    await aRow.getByRole('button', { name: '切换到此角色', exact: true }).click();
    await expect.poll(async()=>{const runtime=(await pet.evaluate(()=>window.aurabot.listProfiles())).runtime;return runtime.online&&runtime.activeId===a;},{timeout:25000}).toBe(true);
    expect((await history(pet, a)).map(message => message.content)).toEqual(expectedA);
    expect(requestCount).toBe(2);
  } finally {
    try {
      releaseFirstTail?.();
      if (app) await close(app);
    } finally {
      provider.closeAllConnections();
      await new Promise<void>(resolve => provider.close(() => resolve()));
      rmSync(userDataDir, { recursive: true, force: true });
    }
  }
});
