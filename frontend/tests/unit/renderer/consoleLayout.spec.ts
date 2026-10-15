/** Exercises the real chat shell with IPC task events. Checks responsive navigation,
 * Completed/paused dismissal persists through snapshots and remounts; resumed tasks reappear.
 * top-bar dismissal, keyboard focus restoration and subscription cleanup through Vue and Pinia. */
// @vitest-environment jsdom
import {createApp, nextTick} from 'vue';
import {createPinia} from 'pinia';
import {afterEach, expect, it, vi} from 'vitest';
import ConsoleWindow from '../../../src/features/console/ConsoleWindow.vue';
let dispose=()=>{};
afterEach(()=>{dispose();localStorage.clear();vi.restoreAllMocks();});
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
it('keeps the top toggle interactive and keyboard reachable while the narrow panel is open',async()=>{
  const {host}=await mount(true);
  const toggle=host.querySelector<HTMLButtonElement>('[aria-controls="console-tasks"]')!;
  toggle.click();await nextTick();
  expect(toggle.closest('[inert]')).toBeNull();
  const close=host.querySelector<HTMLButtonElement>('[role="dialog"] button')!;
  close.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true}));
  expect(document.activeElement).toBe(toggle);
  toggle.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true}));
  expect(document.activeElement).toBe(close);
  toggle.click();await nextTick();
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(host.querySelector('.console-chat')!.hasAttribute('inert')).toBe(false);
  toggle.click();await nextTick();
  toggle.focus();
  toggle.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await nextTick();
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(document.activeElement).toBe(toggle);
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

it('clears completed and paused rows across snapshots and remounts, then shows resumed tasks',async()=>{
 let mounted=await mount(false);
 const rows=[{id:'done',title:'Finished',status:'completed'},{id:'pause',title:'Paused',status:'paused'},{id:'live',title:'Running',status:'running'}];
 mounted.tasksChanged({tasks:rows});await nextTick();
 expect(mounted.host.querySelectorAll('.task-dismiss')).toHaveLength(2);
 mounted.host.querySelector<HTMLButtonElement>('.task-dismiss')!.click();await nextTick();
 expect(mounted.host.textContent).not.toContain('Finished');
 mounted.tasksChanged({tasks:rows});await nextTick();expect(mounted.host.textContent).not.toContain('Finished');
 mounted.host.querySelector<HTMLButtonElement>('.task-dismiss')!.click();await nextTick();
 dispose();mounted=await mount(false);mounted.tasksChanged({tasks:rows});await nextTick();
 expect(mounted.host.querySelectorAll('.task-list li')).toHaveLength(1);
 mounted.tasksChanged({tasks:[rows[0],{...rows[1],status:'running'},rows[2]]});await nextTick();
 expect(mounted.host.querySelectorAll('.task-list li')).toHaveLength(2);
 mounted.tasksChanged({tasks:rows});await nextTick();expect(mounted.host.querySelectorAll('.task-dismiss')).toHaveLength(1);
});
it('includes task clear controls in the narrow panel keyboard loop',async()=>{
 const {host,tasksChanged}=await mount(true);
 tasksChanged({tasks:[{id:'done',title:'Finished',status:'completed'}]});await nextTick();
 host.querySelector<HTMLButtonElement>('[aria-controls="console-tasks"]')!.click();await nextTick();
 const close=host.querySelector<HTMLButtonElement>('[role="dialog"] button')!;
 close.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true}));
 expect(document.activeElement).toBe(host.querySelector('.task-dismiss'));
});
