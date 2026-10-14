/** Reproduces overlapping Profile refreshes with the real development Pinia plugin installed.
 * Electron/Vite development wraps each action in a distinct Proxy. A reconnect must still wait
 * for the newest snapshot before PetApp checks readiness. Controlled IPC replies expose early returns.
 */
// @vitest-environment jsdom
import { createApp } from 'vue';
import { createPinia, disposePinia } from 'pinia';
import { expect, it, vi } from 'vitest';
import { useAuraBotStore } from '../../../src/app/store';

it('waits for the latest Profile snapshot across development action proxies', async () => {
  vi.stubEnv('NODE_ENV', 'development');
  const pinia = createPinia();
  createApp({}).use(pinia);
  const replies: Array<(value: unknown) => void> = [];
  vi.stubGlobal('window', { aurabot: { getInitialState: () => new Promise(resolve => replies.push(resolve)) } });
  try {
    const store = useAuraBotStore(pinia);
    store.profileRuntime = { selectedId: 'a', activeId: null, revision: 1, generation: 1, online: false, activating: true };
    let connected = false;
    const reconnect = store.loadInitialState().then(() => { connected = true; });
    const refresh = store.loadInitialState();
    const ready = { profile: { id: 'a' }, profileRuntime: { selectedId: 'a', activeId: 'a', revision: 1, generation: 2, online: true, activating: false } };
    replies[0]!(ready);
    await new Promise(resolve => setTimeout(resolve, 0));
    const completedBeforeLatest = connected;
    replies[1]!(ready);
    await Promise.all([reconnect, refresh]);
    expect(completedBeforeLatest).toBe(false);
    expect(store.profileRuntime?.online).toBe(true);
    expect(store.profileRuntime?.activating).toBe(false);
  } finally {
    disposePinia(pinia);
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  }
});
