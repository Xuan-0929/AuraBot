/** Loads the compiled Renderer with typed, read-only desktop fixtures instead of Electron/Python.
 * Profile fixtures include independent conversation drafts and speech resource IPC. Each test gets isolated settings and pages; real Vue/CSS layout runs in Chromium. Debug updates
 * cross the same subscription seam as preload. Unexpected renderer errors fail the test, while
 * optional screenshots are artifacts only, not visual regression assertions. */
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test as base, expect, type Page } from '@playwright/test';
import {defaultProfileVoice} from '../../src/shared/contracts/voice';
import { DesktopConfigStore } from '../../src/main/desktopConfigStore';
import { createDefaultProfile } from '../../src/core/profiles/soulService';
import { emptyAvatarAdaptor } from '../../src/shared/contracts/avatar';
import type { AuraBotApi } from '../../src/shared/contracts/ipc';
import type { LiveDebugUpdate } from '../../src/shared/contracts/liveDebug';
import type { ProfileDetail } from '../../src/shared/contracts/profiles';

export const test = base.extend<{ renderer: void }>({
  renderer: [async ({ context }, use) => {
    const root = mkdtempSync(join(tmpdir(), 'aurabot-browser-'));
    const errors: string[] = [];
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    try {
      const config = new DesktopConfigStore(root);
      config.markApplied(config.snapshot());
      config.online = true;
      await context.addInitScript(({ settings, profile, adaptor, voice }) => {
        const runtime = { selectedId: profile.id, activeId: profile.id, revision: 1, generation: 1, online: true };
        const detail: ProfileDetail = {
          saved: { profile, voice, soulText: '# Aura\nA private persona.\n', soulFingerprint: 'fixture', revision: 1, adaptor, modelVersion: '' },
          applied: null, selected: true, memory: { directory: 'profiles/fixture/memory', initialized: false }, resources: []
        };
        const messages = ['帮我梳理一下今天的工作。', '我们先把最重要的部分做好。\n\n统一颜色和操作层级。',
          '配置页面能更紧凑一些吗？', '保存和应用会保留明确的状态提示，你随时知道修改是否已经生效。'].map((content, index) => ({
          id: `layout-${index}`, sessionId: 'layout', profileId: profile.id,
          role: index % 2 ? 'assistant' as const : 'user' as const, content, createdAt: '2025-01-01T00:00:00Z'
        }));
        const subscribe = () => () => {};
        const language = new URL(location.href).searchParams.get('language');
        if (language === 'zh' || language === 'en' || language === 'ja') settings.config.desktop.pet.displayLanguage = language;
        const api = {
          getSettings: async () => settings,
          onSpeechAssetsChanged: subscribe, getSpeechAssets: async () => ({phase:'idle',assets:[],paths:{silero_model_path:'',silero_license_path:'',smart_turn_model_path:'',smart_turn_license_path:''},received:0,total:1}),
          getInitialState: async () => ({ profile, profileRuntime: runtime, mainSessionId: 'layout', messages,
            agentStatus: 'idle' as const, petSettings: settings.config.desktop.pet,
            live2dModelState: { currentModelId: '', currentModelPath: '', models: [] }, }),
          getHistory: async () => ({ messages }),
          listProfiles: async () => ({ runtime, profiles: [{ id: profile.id, displayName: profile.displayName,
            selected: true, revision: 1, appliedRevision: null, modelName: null, visualStatus: 'unconfigured' as const }] }),
          getProfile: async () => detail,
          subscribeTasks: async () => {},
          onTasksChanged: listener => {
            listener({ tasks: [{ id: 'layout-task', title: '整理设计系统', status: 'running' }] });
            return () => {};
          },
          getLiveDebugState: async () => ({ revision: 0, events: [], state: {}, enabled: true, captureId: 'layout',
            dropped: 0, logDirectory: 'logs/layout', updatedAt: 0 }),
          onLiveDebugChanged: listener => {
            const receive = (event: Event) => listener((event as CustomEvent<LiveDebugUpdate>).detail);
            window.addEventListener('test:debug-update', receive);
            return () => window.removeEventListener('test:debug-update', receive);
          },
          onSettingsChanged: subscribe, onProfilesChanged: subscribe, onRealtimeError: subscribe,
          onPetSettingsChanged: subscribe, onPetActivity: subscribe, onLive2DModelStateChanged: subscribe,
          onPetCursorPosition: subscribe, onAgentStatusChanged: subscribe,
          onChatMessageUpdated: subscribe
        } satisfies Partial<AuraBotApi>;
        Object.defineProperty(window, 'aurabot', { value: api });
      }, { settings: config.status(), profile: createDefaultProfile('2025-01-01T00:00:00Z').profile, adaptor: emptyAvatarAdaptor(), voice:defaultProfileVoice() });
      await use();
      expect(errors, 'Renderer must not call unsupported IPC or throw').toEqual([]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  }, { auto: true }]
});
export { expect };

export async function capture(page: Page, name: string) {
  const directory = process.env.AURABOT_VISUAL_OUTPUT_DIR;
  if (!directory) return;
  mkdirSync(directory, { recursive: true });
  await page.screenshot({ animations: 'disabled', path: join(directory, name + '.png') });
}
