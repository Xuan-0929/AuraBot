/** Verifies source-independent first-run installation paths and preservation of saved user workspaces.
 * Exercises DesktopConfigStore against temporary directories; also verifies legacy search key normalization and redaction; no Electron or native database is needed.
 */
import { mkdtempSync, realpathSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { expect, it } from 'vitest';
import { DesktopConfigStore } from '../../../src/main/desktopConfigStore';

it('registers installation/home defaults only for a fresh configuration', () => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'aura-install-')));
  try {
    const source = resolve(root, '../separate-source-checkout');
    const store = new DesktopConfigStore(root);
    const config = store.snapshot();
    expect(config.backend.app.workspaces).toEqual([{id:'default',path:root},{id:'home',path:homedir()}]);
    expect(config.backend.task.pi_runtime_dir).toBe(join(root,'pi-runtime'));
    expect(config.backend.task.user_data_dir).toBe(root);
    expect(resolve(config.backend.app.data_dir,config.backend.main_agent.profile_dir)).toBe(join(root,'profiles'));
    config.backend.app.workspaces=[{id:'my-project',path:source}];store.save(config);
    const reopened = new DesktopConfigStore(root);
    expect(reopened.snapshot().backend.app.workspaces).toEqual(config.backend.app.workspaces);
    expect(reopened.snapshot().backend.task.pi_runtime_dir).toBe(join(root,'pi-runtime'));
  } finally { rmSync(root,{recursive:true,force:true}); }
});

it('uses Node for Electron regardless of its executable filename and repairs the old default', () => {
  const root=mkdtempSync(join(tmpdir(),'aura-electron-node-'));
  const original=Object.getOwnPropertyDescriptor(process.versions,'electron');
  Object.defineProperty(process.versions,'electron',{value:'41.0.0',configurable:true});
  try {
    const config=new DesktopConfigStore(root).snapshot();
    expect(config.backend.task.node_executable).toBe('node');
    config.backend.task.node_executable=process.execPath;
    writeFileSync(join(root,'settings.json'),JSON.stringify(config));
    expect(new DesktopConfigStore(root).snapshot().backend.task.node_executable).toBe('node');
    config.backend.task.node_executable=join(root,'custom-node.exe');
    writeFileSync(join(root,'settings.json'),JSON.stringify(config));
    expect(new DesktopConfigStore(root).snapshot().backend.task.node_executable).toBe(config.backend.task.node_executable);
  } finally {
    if(original)Object.defineProperty(process.versions,'electron',original);else Reflect.deleteProperty(process.versions,'electron');
    rmSync(root,{recursive:true,force:true});
  }
});


it('normalizes legacy search secrets and persists only generic fields', () => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'aura-search-config-')));
  try {
    const original = new DesktopConfigStore(root).snapshot();
    const legacy = JSON.parse(JSON.stringify(original));
    legacy.backend.main_agent.web = { tavily_api_key: 'legacy-search-key' };
    writeFileSync(join(root, 'settings.json'), JSON.stringify(legacy));
    const migrated = new DesktopConfigStore(root);
    expect(migrated.snapshot().backend.main_agent.web).toEqual({provider:'tavily',api_key:'legacy-search-key'});
    expect(migrated.publicConfig().backend.main_agent.web.api_key).toBe('');
    migrated.save(migrated.publicConfig());
    expect(readFileSync(join(root,'settings.json'),'utf8')).not.toContain('tavily_api_key');
    expect(migrated.snapshot().backend.main_agent.web.api_key).toBe('legacy-search-key');
    legacy.backend.main_agent.web.api_key = '';
    writeFileSync(join(root, 'settings.json'), JSON.stringify(legacy));
    expect(new DesktopConfigStore(root).snapshot().backend.main_agent.web.api_key).toBe('');
    legacy.backend.main_agent.web.provider = 'infoquest';
    writeFileSync(join(root, 'settings.json'), JSON.stringify(legacy));
    expect(() => new DesktopConfigStore(root)).toThrow();
  } finally { rmSync(root,{recursive:true,force:true}); }
});
