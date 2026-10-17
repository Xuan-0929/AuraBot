/** Exercises settings draft/apply IPC and compact advanced controls through the real Vue form.
 * History display clearing invokes its own immediate IPC without saving or discarding settings drafts.
 * Controlled desktop configuration fixtures verify save safety, native invalid-field revelation,
 * recoverable server errors, and masked-key preservation/replacement/deletion through the real save
 * store, separate workspace and pet status navigation with actionable runtime reminders and localized status labels and native search-provider dropdown selection, including length-matched first/last-four previews and launch-gated debug actions that preserve unrelated drafts, without starting Electron or a backend.
 * Global navigation excludes voice; Profile editor tests cover conversation settings and mode changes.
 * Provider help buttons dispatch fixed document identities without saving and surface browser-opening failures. */
// @vitest-environment jsdom
import { createApp, nextTick } from 'vue';
import { createPinia } from 'pinia';
import { afterEach, expect, it, vi } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import SettingsWindow from '../../../src/features/settings/SettingsWindow.vue';
import { DesktopConfigStore } from '../../../src/main/desktopConfigStore';
import { t } from '../../../src/i18n';
import {useAuraBotStore} from '../../../src/app/store';
let dispose:()=>void=()=>{};
afterEach(()=>{dispose();vi.unstubAllGlobals();});
it('shows missing Git Bash only as a brief pet-status hint without an alert',async()=>{
 const root=mkdtempSync(join(tmpdir(),'aura-bash-hint-'));const config=new DesktopConfigStore(root);
 config.online=true;config.capabilities={text:true,voice:true,tasks:false};config.taskNotice='git_bash_missing';
 Object.assign(window,{aurabot:{getSettings:async()=>config.status(),onSettingsChanged:()=>()=>{}}});
 const host=document.createElement('div');document.body.append(host);const app=createApp(SettingsWindow);app.use(createPinia());app.mount(host);
 dispose=()=>{app.unmount();host.remove();rmSync(root,{recursive:true,force:true});};
 await vi.waitFor(()=>expect(host.querySelector('form')).not.toBeNull());
 expect(host.textContent).not.toContain('Git Bash');expect(host.querySelector('[role=alert]')).toBeNull();
 [...host.querySelectorAll<HTMLButtonElement>('nav button')].find(button=>button.textContent==='桌宠状态')!.click();await nextTick();
 expect(host.textContent).toContain(t('zh','gitBashMissing'));expect(host.textContent).not.toContain(t('zh','tasksNotConfigured'));
 expect(host.querySelector('[role=alert]')).toBeNull();expect(host.querySelector('[role=alertdialog]')).toBeNull();
 expect([...host.querySelectorAll<HTMLButtonElement>('button')].find(button=>button.textContent==='应用')!.disabled).toBe(false);
});
it('preserves untouched keys and lets password inputs replace or delete every saved key',async()=>{
 const root=mkdtempSync(join(tmpdir(),'aura-secrets-'));const config=new DesktopConfigStore(root);
 const seed=config.snapshot();seed.backend.chat_model.api_key='chat-fixture';seed.backend.task.model.api_key='task-fixture';seed.backend.main_agent.web.api_key='search-fixture';seed.backend.main_agent.speech.api_key='speech-fixture';config.save(seed);
 const saveSettings=vi.fn(async(value,clear)=>config.save(structuredClone(value),structuredClone(clear)));
 Object.assign(window,{aurabot:{onSpeechAssetsChanged:()=>()=>{},getSpeechAssets:async()=>({phase:'idle',assets:[],paths:{},received:0,total:1}),getSettings:async()=>config.status(),saveSettings,onSettingsChanged:()=>()=>{}}});
 const host=document.createElement('div');document.body.append(host);const app=createApp(SettingsWindow);app.use(createPinia());app.mount(host);
 dispose=()=>{app.unmount();host.remove();rmSync(root,{recursive:true,force:true});};
 await vi.waitFor(()=>expect(host.querySelectorAll('input[data-secret-input]')).toHaveLength(2));
 const keys=()=>[...host.querySelectorAll<HTMLInputElement>('input[data-secret-input]')];
 const edit=async(input:HTMLInputElement,value:string)=>{input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));await nextTick();};
 const save=async()=>{const count=saveSettings.mock.calls.length;host.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));await vi.waitFor(()=>expect(saveSettings).toHaveBeenCalledTimes(count+1));await nextTick();};
 expect(keys().map(input=>input.value)).toEqual(['task••••ture','sear••••••ture']);
 expect(keys().every(input=>input.type==='text')).toBe(true);
 expect(host.querySelector<HTMLSelectElement>('select[name="search-provider"]')?.value).toBe('tavily');
 expect(host.querySelector<HTMLOptionElement>('option[value="infoquest"]')?.disabled).toBe(true);
 expect(host.querySelector('nav button')?.textContent).toBe('能力集成');
 expect(config.publicConfig().backend.chat_model.api_key).toBe('');
 expect(host.textContent).not.toContain('清除已保存密钥');expect(host.textContent).toContain('搜索接口');expect(host.querySelector('.settings-v2-content-header span')).toBeNull();
 await edit(host.querySelector('input')!,'https://example.invalid/v1');await save();
 expect(config.snapshot().backend.chat_model.api_key).toBe('chat-fixture');
 expect(keys()[0]!.value).toBe('task••••ture');
 await edit(keys()[0]!,'');await edit(keys()[1]!,'');await save();
 expect(config.snapshot().backend.task.model.api_key).toBe('');expect(config.snapshot().backend.main_agent.web.api_key).toBe('');
 expect(saveSettings.mock.calls.every(([value])=>!JSON.stringify(value).includes('•'))).toBe(true);
});
it('requires save before apply and defaults the busy dialog to cancel',async()=>{
 const root=mkdtempSync(join(tmpdir(),'aura-form-'));const config=new DesktopConfigStore(root);config.markApplied(config.snapshot());config.online=true;
 const saveSettings=vi.fn(async(value,clear)=>config.save(structuredClone(value),structuredClone(clear)));
 const applySettings=vi.fn(async()=>({state:config.status(),busy:{tasks:1,memoryWriting:false,mainAgent:false}}));
 Object.assign(window,{aurabot:{onSpeechAssetsChanged:()=>()=>{},getSpeechAssets:async()=>({phase:'idle',assets:[],paths:{},received:0,total:1}),getSettings:async()=>config.status(),saveSettings,applySettings,onSettingsChanged:()=>()=>{}}});
 const host=document.createElement('div');document.body.append(host);const app=createApp(SettingsWindow);app.use(createPinia());app.mount(host);
 dispose=()=>{app.unmount();host.remove();rmSync(root,{recursive:true,force:true});};
 await vi.waitFor(()=>expect(host.querySelector('input')).not.toBeNull());
 const button=(text:string)=>[...host.querySelectorAll('button')].find(b=>b.textContent===text)!;
 const input=host.querySelector('input')!;input.value='https://example.invalid/v1';input.dispatchEvent(new Event('input',{bubbles:true}));await nextTick();
 expect(button('应用').disabled).toBe(true);button('保存').click();
 await vi.waitFor(()=>expect(saveSettings).toHaveBeenCalledOnce());await nextTick();
 expect(applySettings).not.toHaveBeenCalled();expect(button('应用').disabled).toBe(false);
 button('应用').click();await vi.waitFor(()=>expect(host.querySelector('[role="alertdialog"]')).not.toBeNull());
 expect(document.activeElement).toBe(button('取消'));button('取消').click();await nextTick();
 expect(host.querySelector('[role="alertdialog"]')).toBeNull();await nextTick();expect(document.activeElement).toBe(button('应用'));expect(applySettings).toHaveBeenCalledExactlyOnceWith(false);
});

it('keeps advanced fields collapsed until invalid or a save failure needs attention',async()=>{
 const root=mkdtempSync(join(tmpdir(),'aura-advanced-'));const config=new DesktopConfigStore(root);
 const saveSettings=vi.fn(async()=>{throw new Error('配置校验失败：请检查请求超时');});
 Object.assign(window,{aurabot:{onSpeechAssetsChanged:()=>()=>{},getSpeechAssets:async()=>({phase:'idle',assets:[],paths:{},received:0,total:1}),getSettings:async()=>config.status(),saveSettings,onSettingsChanged:()=>()=>{}}});
 const host=document.createElement('div');document.body.append(host);const app=createApp(SettingsWindow);app.use(createPinia());app.mount(host);
 dispose=()=>{app.unmount();host.remove();rmSync(root,{recursive:true,force:true});};
 await vi.waitFor(()=>expect(host.querySelector('input')).not.toBeNull());
 const advanced=[...host.querySelectorAll('details')];expect(advanced).toHaveLength(1);expect(advanced.every(group=>!group.open)).toBe(true);
 const timeout=advanced[0]!.querySelector('input')!;const invalid=new Event('invalid',{bubbles:false,cancelable:true});timeout.dispatchEvent(invalid);await nextTick();expect(advanced[0]!.open).toBe(true);expect(invalid.defaultPrevented).toBe(false);
 advanced[0]!.open=false;
 host.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
 await vi.waitFor(()=>expect(host.querySelector('[role="alert"]')?.textContent).toContain('配置校验失败'));
 expect(advanced.every(group=>group.open)).toBe(true);
 const navigation=host.querySelectorAll('nav button');expect(navigation).toHaveLength(4);expect(navigation[3]!.textContent).toBe('角色配置');expect(host.querySelectorAll('nav svg')).toHaveLength(4);expect([...host.querySelectorAll('nav svg')].every(icon=>icon.getAttribute('viewBox')==='0 0 24 24')).toBe(true);
 (navigation[2] as HTMLButtonElement).click();await nextTick();
 expect(host.textContent).toContain('向 Main Agent 提供');expect(host.textContent).not.toContain('只能访问');
 expect(host.querySelectorAll('details input')).toHaveLength(2);
 expect(host.querySelector('details')?.textContent).toContain('Node 可执行文件');
 (navigation[1] as HTMLButtonElement).click();await nextTick();
 expect(host.querySelectorAll('details input')).toHaveLength(0);
 expect(navigation[1]!.textContent).toBe('桌宠状态');expect(host.textContent).toContain('运行提示');expect(host.textContent).not.toContain('服务已连接');expect(host.textContent).toContain('桌宠鼠标穿透');expect(host.textContent).toContain('服务未连接');
 const language=host.querySelector('select')!;language.value='en';language.dispatchEvent(new Event('change',{bubbles:true}));await nextTick();
 expect(host.querySelector('.settings-v2-sidebar-brand')?.textContent).toBe('Settings');expect(navigation[0]!.textContent).toBe('Capability integrations');expect(host.querySelector('.settings-save-bar [role=status]')?.textContent).toBe('Unsaved');
 expect(host.textContent).toContain(t('en','displayLanguage'));expect(host.textContent).toContain(t('en','clickThroughPet'));
 language.value='ja';language.dispatchEvent(new Event('change',{bubbles:true}));await nextTick();expect(host.querySelector('.settings-v2-sidebar-brand')?.textContent).toBe('設定');expect(navigation[0]!.textContent).toBe('機能連携');
 expect(host.textContent).toContain(t('ja','displayLanguage'));expect(host.textContent).toContain(t('ja','clickThroughPet'));

});

it.each([false,true])('shows debug actions only for a debug launch (%s), without altering drafts',async(debug)=>{
 const root=mkdtempSync(join(tmpdir(),'aura-debug-setting-'));const config=new DesktopConfigStore(root,{debug,lockMemory:false});config.markApplied(config.snapshot());config.online=true;
 let changed:(value:ReturnType<typeof config.status>)=>void=()=>{};
 const openLiveDebugWindow=vi.fn(async()=>{});
 const saveSettings=vi.fn(async(value)=>config.save(structuredClone(value)));
 Object.assign(window,{aurabot:{getSettings:async()=>config.status(),saveSettings,openLiveDebugWindow,onSettingsChanged:(listener:typeof changed)=>{changed=listener;return ()=>{};}}});
 const host=document.createElement('div');document.body.append(host);const app=createApp(SettingsWindow);app.use(createPinia());app.mount(host);
 dispose=()=>{app.unmount();host.remove();rmSync(root,{recursive:true,force:true});};
 await vi.waitFor(()=>expect(host.querySelector('input')).not.toBeNull());
 const address=host.querySelector('input')!;address.value='https://draft.example/v1';address.dispatchEvent(new Event('input',{bubbles:true}));await nextTick();
 const navigation=host.querySelectorAll<HTMLButtonElement>('nav button');navigation[1]!.click();await nextTick();
 expect(host.querySelector('[name="live-debug-enabled"]')).toBeNull();
 expect(Boolean(host.querySelector('[data-debug-settings]'))).toBe(debug);
 if(debug){host.querySelector<HTMLButtonElement>('.debug-actions button')!.click();await nextTick();expect(openLiveDebugWindow).toHaveBeenCalledOnce();}
 changed(config.status());await nextTick();navigation[0]!.click();await nextTick();
 expect(host.querySelector('input')!.value).toBe('https://draft.example/v1');
 expect(host.querySelector<HTMLButtonElement>('button[type=submit]')!.disabled).toBe(false);
 expect(saveSettings).not.toHaveBeenCalled();
});

it('clears only the current profile history display immediately while keeping settings drafts',async()=>{
 const root=mkdtempSync(join(tmpdir(),'aura-history-setting-'));const config=new DesktopConfigStore(root);
 const clearHistoryDisplay=vi.fn(async()=>{}),saveSettings=vi.fn();
 Object.assign(window,{aurabot:{getSettings:async()=>config.status(),onSettingsChanged:()=>()=>{},clearHistoryDisplay,saveSettings}});
 const host=document.createElement('div');document.body.append(host);const app=createApp(SettingsWindow),pinia=createPinia();app.use(pinia);
 useAuraBotStore(pinia).profile={id:'current',displayName:'Current',active:true,createdAt:'now',updatedAt:'now'};
 app.mount(host);dispose=()=>{app.unmount();host.remove();rmSync(root,{recursive:true,force:true});};
 await vi.waitFor(()=>expect(host.querySelector('input')).not.toBeNull());
 const address=host.querySelector('input')!;address.value='https://draft.example/v1';address.dispatchEvent(new Event('input',{bubbles:true}));await nextTick();
 const navigation=host.querySelectorAll<HTMLButtonElement>('nav button');navigation[1]!.click();await nextTick();
 [...host.querySelectorAll('button')].find(button=>button.textContent==='清除历史会话记录显示')!.click();
 await vi.waitFor(()=>expect(clearHistoryDisplay).toHaveBeenCalledExactlyOnceWith('current'));
 await vi.waitFor(()=>expect(host.textContent).toContain('原始记录仍保留'));expect(saveSettings).not.toHaveBeenCalled();
 navigation[0]!.click();await nextTick();expect(host.querySelector('input')!.value).toBe('https://draft.example/v1');
});
