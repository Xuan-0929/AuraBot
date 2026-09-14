// @vitest-environment jsdom
/** Exercises rendered pet controls and window hover using mocked desktop/media boundaries;
 * verifies microphone status and capture interactions without starting a Live2D renderer. */
import { createApp, defineComponent, h, nextTick, reactive } from 'vue';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ControlButtons from '../../../src/features/pet-vue/components/ControlButtons.vue';

let cleanup: (() => void) | undefined;
afterEach(() => { cleanup?.(); cleanup = undefined; });
function mountControls() {
  const props = reactive({ visible: false, displayLanguage: 'en' as const, voiceInputActive: false, voiceInputState: 'off' as 'off' | 'connecting' | 'active' | 'error' });
  const onDraggingChange = vi.fn();
  const host = document.createElement('div');
  document.body.append(host);
  const app = createApp(defineComponent(() => () => h(ControlButtons, { ...props, onDraggingChange })));
  app.mount(host);
  cleanup = () => { app.unmount(); host.remove(); };
  return { host, props, onDraggingChange };
}

describe('pet quick controls', () => {
  it('shows a persistent mic status only for microphone activity, pending connection or error', async () => {
    const { host, props } = mountControls();
    const mic = host.querySelector('.pet-action-voice')!;
    expect(mic.getAttribute('data-persistent')).toBe('false');
    props.voiceInputState = 'active';
    await nextTick();
    expect(mic.getAttribute('data-persistent')).toBe('false');
    props.voiceInputActive = true;
    await nextTick();
    expect(mic.getAttribute('data-persistent')).toBe('true');
    expect(mic.getAttribute('aria-pressed')).toBe('true');
    props.voiceInputActive = false;
    for (const state of ['connecting', 'error'] as const) {
      props.voiceInputState = state;
      await nextTick();
      expect(mic.getAttribute('data-persistent')).toBe('true');
    }
  });

  it('retains drag capture and publishes interaction state until release', async () => {
    const { host, onDraggingChange } = mountControls();
    const button = host.querySelector('.pet-action-drag') as HTMLButtonElement;
    Object.assign(button, { setPointerCapture: vi.fn(), hasPointerCapture: () => true, releasePointerCapture: vi.fn() });
    const movePetWindowBy = vi.fn();
    Object.assign(window, { aurabot: { movePetWindowBy } });
    function pointer(type: string, screenX: number, screenY: number) {
      const event = new Event(type, { bubbles: true });
      Object.assign(event, { pointerId: 7, screenX, screenY });
      button.dispatchEvent(event);
    }
    pointer('pointerdown', 20, 30);
    await nextTick();
    expect(onDraggingChange).toHaveBeenLastCalledWith(true);
    pointer('pointermove', 25, 38);
    expect(movePetWindowBy).toHaveBeenCalledWith({ x: 5, y: 8 });
    pointer('pointercancel', 25, 38);
    await nextTick();
    expect(onDraggingChange).toHaveBeenLastCalledWith(false);
    expect(button.releasePointerCapture).toHaveBeenCalledWith(7);
  });
});

let realtimeCallbacks: { onState: (state: string, detail?: string) => void };
let toggleError: Error | undefined;
vi.mock('../../../src/app/store', () => ({ useAuraBotStore: () => petStore }));
vi.mock('../../../src/main-agent-realtime/client', () => ({ MainAgentRealtimeClient: class {
  voiceEnabled = false;
  constructor(public options: { onState: (state: string, detail?: string) => void }) { realtimeCallbacks = options; }
  async toggle() { if (toggleError) throw toggleError; this.voiceEnabled = !this.voiceEnabled; this.options.onState('active'); }
  async start() { starts++;this.options.onState('active'); }
  async stop() {}
} }));
vi.mock('../../../src/features/pet-vue/live2d/Scene.vue', () => ({ default: defineComponent({ setup(_, { expose }) {
  expose({ ready: async () => ({ available: true }), setState: () => {} });
  return () => h('canvas');
} }) }));
import PetApp from '../../../src/features/pet-vue/PetApp.vue';
let starts=0;
const petStore = reactive({
  agentStatus: 'idle', displayLanguage: 'en', petSettings: { clickThroughPet: true }, profile: { id: 'a' },
  profileRuntime:{activating:false,online:true,activeId:'a' as string|null,revision:1,error:undefined as string|undefined},
  petCursorPosition: undefined as undefined | { localX: number; localY: number },
  loadInitialState: async () => {}, armPetActivityAutoClear: () => {}, openConsoleWindow: vi.fn()
});
function mountPet(ready=true) {
  petStore.profileRuntime={activating:false,online:ready,activeId:ready?'a':null,revision:1,error:undefined};
  toggleError = undefined;
  petStore.petCursorPosition = undefined;
  Object.assign(window, { aurabot: {
    onPetCommand: () => () => {}, petReady: async () => {}, getSettings: async () => ({ online: true }),
    setPetMousePassthrough: vi.fn(async () => {}), resizePetWindowCorner: vi.fn(async () => {}), reportRealtimeError: async () => {}
  } });
  const host = document.createElement('div'); document.body.append(host);
  const app = createApp(PetApp); app.mount(host);
  cleanup = () => { app.unmount(); host.remove(); };
  return host;
}
it('reveals controls from existing cursor events, hides on exit and retains keyboard focus', async () => {
  const host = mountPet();
  const controls = host.querySelector('.pet-quick-controls')!;
  await nextTick();
  expect(controls.getAttribute('data-visible')).toBe('false');
  petStore.petCursorPosition = { localX: 100, localY: 100 };
  await nextTick();
  expect(controls.getAttribute('data-visible')).toBe('true');
  const chat = host.querySelector('.pet-action-console') as HTMLButtonElement;
  chat.focus();
  petStore.petCursorPosition = { localX: -1, localY: -1 };
  await nextTick();
  expect(controls.getAttribute('data-visible')).toBe('true');
  chat.blur();
  await nextTick();
  expect(controls.getAttribute('data-visible')).toBe('false');
});
it('does not mark the microphone active when only the text transport is connected', async () => {
  const host = mountPet();
  await vi.waitFor(() => expect(host.querySelector('.pet-action-voice')?.getAttribute('data-state')).toBe('off'));
  (host.querySelector('.pet-action-voice') as HTMLButtonElement).click();
  await vi.waitFor(() => expect(host.querySelector('.pet-action-voice')?.getAttribute('aria-pressed')).toBe('true'));
});

it('keeps all eight resize directions captured across window exit and releases on cancellation', async () => {
  const host = mountPet();
  const root = host.querySelector('main')!;
  const controls = host.querySelector('.pet-quick-controls')!;
  for (const direction of ['n', 's', 'e', 'w', 'nw', 'ne', 'sw', 'se']) {
    const grip = host.querySelector(`.pet-vue-resize-grip--${direction}`) as HTMLElement;
    Object.assign(grip, { setPointerCapture: vi.fn(), hasPointerCapture: () => true, releasePointerCapture: vi.fn() });
    function send(type: string) {
      const event = new Event(type, { bubbles: true });
      Object.assign(event, { pointerId: 9, movementX: 3, movementY: -2 });
      grip.dispatchEvent(event);
    }
    send('pointerdown');
    root.dispatchEvent(new Event('pointerleave'));
    await nextTick();
    expect(root.getAttribute('data-resizing')).toBe('true');
    expect(controls.getAttribute('data-visible')).toBe('true');
    send('pointermove');
    expect(window.aurabot.resizePetWindowCorner).toHaveBeenLastCalledWith(direction, { x: 3, y: -2 });
    send('pointercancel');
    await nextTick();
    expect(root.getAttribute('data-resizing')).toBe('false');
    expect(grip.releasePointerCapture).toHaveBeenCalledWith(9);
  }
});

it('keeps text transport failures out of the persistent microphone status', async () => {
  const host = mountPet();
  await new Promise(resolve => setTimeout(resolve, 0));
  realtimeCallbacks.onState('error', 'Text transport disconnected');
  await nextTick();
  expect(host.querySelector('[role="alert"]')?.textContent).toBe('Text transport disconnected');
  const mic = host.querySelector('.pet-action-voice')!;
  expect(mic.getAttribute('data-state')).toBe('off');
  expect(mic.getAttribute('data-persistent')).toBe('false');
});

it('keeps microphone failures visible until a successful retry', async () => {
  const host = mountPet();
  await new Promise(resolve => setTimeout(resolve, 0));
  toggleError = new Error('Microphone access denied');
  const mic = host.querySelector('.pet-action-voice') as HTMLButtonElement;
  mic.click();
  await vi.waitFor(() => expect(mic.getAttribute('data-state')).toBe('error'));
  expect(mic.getAttribute('data-persistent')).toBe('true');
  expect(host.querySelector('[role="alert"]')?.textContent).toBe('Microphone access denied');
  toggleError = undefined;
  mic.click();
  await vi.waitFor(() => expect(mic.getAttribute('aria-pressed')).toBe('true'));
  realtimeCallbacks.onState('error', 'Connection lost during capture');
  await nextTick();
  expect(mic.getAttribute('data-state')).toBe('error');
  expect(mic.getAttribute('data-persistent')).toBe('true');
});

it('waits for Profile activation even when Backend is already online',async()=>{
 starts=0;const host=mountPet(false);await new Promise(r=>setTimeout(r,0));
 const mic=host.querySelector('.pet-action-voice') as HTMLButtonElement;
 expect(starts).toBe(0);expect(mic.disabled).toBe(true);expect(mic.title).toContain('Preparing character');
 petStore.profileRuntime={activating:false,online:true,activeId:'a',revision:1,error:undefined};await nextTick();
 expect(mic.disabled).toBe(false);
 petStore.profileRuntime.activating=true;await nextTick();expect(mic.disabled).toBe(true);
 petStore.profileRuntime={activating:false,online:false,activeId:null,revision:1,error:'Activation failed'};await nextTick();
 expect(mic.disabled).toBe(true);expect(mic.title).toBe('Activation failed');
});
