/** Verifies voice-mode migration and SeedDuplex credential handling through the real settings store.
 * Reloads persisted configuration and exercises the renderer-safe save/clear path, so switching
 * engines cannot leak credentials or silently erase the other provider's configuration. Legacy voice enable flags are discarded; microphone sessions own activation.
 */
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import { DesktopConfigStore } from '../../../src/main/desktopConfigStore';

const roots:string[]=[];
function store(){const root=mkdtempSync(join(tmpdir(),'aura-voice-config-'));roots.push(root);return new DesktopConfigStore(root);}
afterEach(()=>roots.splice(0).forEach(root=>rmSync(root,{recursive:true,force:true})));

it('migrates old settings to pipeline without discarding its credentials',()=>{
  const original=store();const legacy:any=original.snapshot();
  delete legacy.backend.main_agent.speech.mode;delete legacy.backend.main_agent.speech.realtime;
  legacy.backend.main_agent.speech.api_key='pipeline-key';legacy.backend.main_agent.speech.enabled=false;
  writeFileSync(original.path,JSON.stringify(legacy));
  const restored=new DesktopConfigStore(original.root);
  expect(restored.snapshot().backend.main_agent.speech.mode).toBe('pipeline');
  expect(restored.snapshot().backend.main_agent.speech).not.toHaveProperty('enabled');
  expect(restored.snapshot().backend.main_agent.speech.api_key).toBe('pipeline-key');
});

it('keeps realtime credentials secret across save, reload, mode changes and explicit clear',()=>{
  const config=store();const draft=config.publicConfig();
  draft.backend.main_agent.speech.mode='realtime';
  draft.backend.main_agent.speech.realtime.api_key='seeduplex-private-key';
  draft.backend.main_agent.speech.realtime.voice='voice-id';
  config.save(draft);
  expect(config.publicConfig().backend.main_agent.speech.realtime.api_key).toBe('');
  expect(config.status().secrets['main_agent.speech.realtime.api_key']).toBe(true);
  const next=config.publicConfig();next.backend.main_agent.speech.mode='pipeline';config.save(next);
  const restored=new DesktopConfigStore(config.root);
  expect(restored.snapshot().backend.main_agent.speech.realtime.api_key).toBe('seeduplex-private-key');
  restored.save(restored.publicConfig(),['main_agent.speech.realtime.api_key']);
  expect(restored.snapshot().backend.main_agent.speech.realtime.api_key).toBe('');
});
