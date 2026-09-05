/** Exercises the real settings form: saving never applies; a busy restart needs a separate confirmation. */
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
 expect(host.querySelector('[role="alertdialog"]')).toBeNull();expect(applySettings).toHaveBeenCalledExactlyOnceWith(false);
});
