/** Exercises the real chat shell with IPC task events. Checks responsive navigation,
 * focus restoration and subscription cleanup without encoding CSS implementation. */
// @vitest-environment jsdom
import {createApp, nextTick} from 'vue';
import {createPinia} from 'pinia';
import {afterEach, expect, it, vi} from 'vitest';
import ConsoleWindow from '../../../src/features/console/ConsoleWindow.vue';
let dispose=()=>{};
afterEach(()=>{dispose();vi.restoreAllMocks();});
async function mount(narrow:boolean){
  let change:(event:{matches:boolean})=>void=()=>{};
  Object.defineProperty(window,'matchMedia',{configurable:true,value:()=>({matches:narrow,addEventListener:(_name:string,fn:typeof change)=>{change=fn;},removeEventListener:vi.fn()})});
  const unsubscribe=vi.fn();
  let tasksChanged:(value:unknown)=>void=()=>{};
  Object.assign(window,{aurabot:{subscribeTasks:vi.fn(async()=>{}),onTasksChanged:(fn:typeof tasksChanged)=>{tasksChanged=fn;return unsubscribe;}}});
  const host=document.createElement('div');document.body.append(host);
  const app=createApp(ConsoleWindow);app.use(createPinia());app.mount(host);await nextTick();
  dispose=()=>{app.unmount();host.remove();};
  return {host,change,unsubscribe,tasksChanged};
}
it('opens a narrow task panel, traps focus and restores the trigger on Escape',async()=>{
  const {host}=await mount(true);
  const toggle=host.querySelector<HTMLButtonElement>('[aria-controls="console-tasks"]');
  expect(toggle).not.toBeNull();expect(toggle!.getAttribute('aria-expanded')).toBe('false');
  toggle!.click();await nextTick();
  const panel=host.querySelector<HTMLElement>('[role="dialog"]');expect(panel).not.toBeNull();
  expect(panel!.contains(document.activeElement)).toBe(true);
  panel!.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await nextTick();
  expect(toggle!.getAttribute('aria-expanded')).toBe('false');expect(document.activeElement).toBe(toggle);
});
it('shows task updates in wide mode and closes the overlay when crossing to narrow',async()=>{
  const {host,change,tasksChanged,unsubscribe}=await mount(false);
  const toggle=host.querySelector<HTMLButtonElement>('[aria-controls="console-tasks"]');expect(toggle).not.toBeNull();
  expect(toggle!.getAttribute('aria-expanded')).toBe('true');
  tasksChanged({tasks:[{id:'1',title:'Draft notes',status:'running'}]});await nextTick();expect(host.textContent).toContain('Draft notes');
  toggle!.click();await nextTick();
  change({matches:true});await nextTick();expect(toggle!.getAttribute('aria-expanded')).toBe('false');
  change({matches:false});await nextTick();expect(toggle!.getAttribute('aria-expanded')).toBe('false');
  dispose();dispose=()=>{};expect(unsubscribe).toHaveBeenCalledOnce();expect(window.aurabot.subscribeTasks).toHaveBeenLastCalledWith(false);
});
