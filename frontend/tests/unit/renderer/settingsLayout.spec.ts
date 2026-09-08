/** Exercises settings draft/apply IPC and compact advanced controls through the real Vue form.
 * Controlled desktop configuration fixtures verify save safety, native invalid-field revelation,
 * and recoverable server errors without starting Electron or a backend. */
// @vitest-environment jsdom
import { createApp, nextTick } from 'vue';
import { createPinia } from 'pinia';
import { afterEach, expect, it, vi } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import SettingsWindow from '../../../src/features/settings/SettingsWindow.vue';
import { DesktopConfigStore } from '../../../src/main/desktopConfigStore';
let dispose:()=>void=()=>{};
afterEach(()=>{dispose();vi.unstubAllGlobals();});
it('requires save before apply and defaults the busy dialog to cancel',async()=>{
 const root=mkdtempSync(join(tmpdir(),'aura-form-'));const config=new DesktopConfigStore(root,root);config.markApplied(config.snapshot());config.online=true;
 const saveSettings=vi.fn(async(value,clear)=>config.save(structuredClone(value),structuredClone(clear)));
 const applySettings=vi.fn(async()=>({state:config.status(),busy:{tasks:1,memoryWriting:false,mainAgent:false}}));
 Object.assign(window,{aurabot:{getSettings:async()=>config.status(),saveSettings,applySettings,onSettingsChanged:()=>()=>{}}});
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
 const root=mkdtempSync(join(tmpdir(),'aura-advanced-'));const config=new DesktopConfigStore(root,root);
 const saveSettings=vi.fn(async()=>{throw new Error('配置校验失败：请检查请求超时');});
 Object.assign(window,{aurabot:{getSettings:async()=>config.status(),saveSettings,onSettingsChanged:()=>()=>{}}});
 const host=document.createElement('div');document.body.append(host);const app=createApp(SettingsWindow);app.use(createPinia());app.mount(host);
 dispose=()=>{app.unmount();host.remove();rmSync(root,{recursive:true,force:true});};
 await vi.waitFor(()=>expect(host.querySelector('input')).not.toBeNull());
 const advanced=[...host.querySelectorAll('details')];expect(advanced).toHaveLength(2);expect(advanced.every(group=>!group.open)).toBe(true);
 const timeout=advanced[0]!.querySelector('input')!;const invalid=new Event('invalid',{bubbles:false,cancelable:true});timeout.dispatchEvent(invalid);await nextTick();expect(advanced[0]!.open).toBe(true);expect(advanced[1]!.open).toBe(false);expect(invalid.defaultPrevented).toBe(false);
 advanced[0]!.open=false;
 host.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
 await vi.waitFor(()=>expect(host.querySelector('[role="alert"]')?.textContent).toContain('配置校验失败'));
 expect(advanced.every(group=>group.open)).toBe(true);
 const navigation=host.querySelectorAll('nav button');expect(navigation).toHaveLength(6);expect(navigation[4]!.textContent).toBe('角色配置');expect(host.querySelectorAll('nav svg')).toHaveLength(6);expect([...host.querySelectorAll('nav svg')].every(icon=>icon.getAttribute('viewBox')==='0 0 24 24')).toBe(true);
 (navigation[1] as HTMLButtonElement).click();await nextTick();
 expect(host.querySelectorAll('details input')).toHaveLength(6);
 expect([...host.querySelectorAll('label')].find(label=>label.textContent==='音色')?.closest('details')).toBeNull();
 (navigation[3] as HTMLButtonElement).click();await nextTick();
 expect(host.querySelectorAll('details input')).toHaveLength(2);
 expect(host.querySelector('details')?.textContent).toContain('Node 可执行文件');

});
