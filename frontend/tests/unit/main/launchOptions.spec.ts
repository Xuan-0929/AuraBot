/** Exercises process-scoped launch state through its backend environment and real temporary storage.
 * Configuration reloads must strip legacy debug preferences and never persist launch options. */
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { LaunchSession, readLaunchOptions } from '../../../src/main/launchOptions';
import { DesktopConfigStore } from '../../../src/main/desktopConfigStore';

it.each([[], ['--debug'], ['--lock-memory'], ['--debug', '--lock-memory']])('isolates each application launch: %j', (...args:string[]) => {
 const flags=args.filter(value=>typeof value==='string');
 const session=new LaunchSession(readLaunchOptions(flags.map(flag=>flag==='--debug'?'--aurabot-debug':'--aurabot-lock-memory')));
 const second=new LaunchSession(session.options);
 try {
  expect(session.options).toEqual({debug:flags.includes('--debug'),lockMemory:flags.includes('--lock-memory')});
  expect(Object.isFrozen(session.options)).toBe(true);
  const env=session.backendEnvironment();
  expect(env.AURABOT_DEBUG).toBe(flags.includes('--debug')?'1':'0');
  expect(env.AURABOT_LOCK_MEMORY).toBe(flags.includes('--lock-memory')?'1':'0');
  expect(session.backendEnvironment()).toEqual(env);
  if(session.options.lockMemory){
   expect(existsSync(env.AURABOT_SESSION_DATA_DIR)).toBe(true);
   expect(second.backendEnvironment().AURABOT_SESSION_DATA_DIR).not.toBe(env.AURABOT_SESSION_DATA_DIR);
   session.close();expect(existsSync(env.AURABOT_SESSION_DATA_DIR)).toBe(false);
  }else expect(env.AURABOT_SESSION_DATA_DIR).toBe('');
 }finally{session.close();second.close();}
});

it('ignores a legacy enabled setting and keeps launch state outside saved configuration',()=>{
 const root=mkdtempSync(join(tmpdir(),'aurabot-launch-config-'));
 try{
  const original=new DesktopConfigStore(root);
  const legacy=JSON.parse(readFileSync(original.path,'utf8'));legacy.desktop.liveDebugEnabled=true;
  writeFileSync(original.path,JSON.stringify(legacy));
  const normal=new DesktopConfigStore(root);
  expect(normal.status().launchOptions).toEqual({debug:false,lockMemory:false});
  expect(normal.snapshot().desktop).not.toHaveProperty('liveDebugEnabled');
  const debug=new DesktopConfigStore(root,{debug:true,lockMemory:true});
  debug.save(debug.publicConfig());
  expect(debug.status().launchOptions).toEqual({debug:true,lockMemory:true});
  expect(readFileSync(debug.path,'utf8')).not.toMatch(/liveDebugEnabled|launchOptions|lockMemory/);
 }finally{rmSync(root,{recursive:true,force:true});}
});
