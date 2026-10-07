/** Owns immutable application launch flags and the temporary Memory/Context directory for one run.
 * Main reads the launcher's namespaced argv switches once, supplies the same explicit environment to every Backend child, and closes
 * this session only after Backend storage is closed. No launch state enters settings.json. */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { LaunchOptions } from '../shared/contracts/desktop';

export function readLaunchOptions(argv:readonly string[]):LaunchOptions {
  return Object.freeze({debug:argv.includes('--aurabot-debug'),lockMemory:argv.includes('--aurabot-lock-memory')});
}

export class LaunchSession {
  readonly options:LaunchOptions;
  private readonly directory:string;
  constructor(options:LaunchOptions) {
    this.options=Object.freeze({...options});
    this.directory=options.lockMemory?mkdtempSync(join(tmpdir(),'aurabot-memory-')):'';
  }
  backendEnvironment():Record<string,string> {
    return {AURABOT_DEBUG:this.options.debug?'1':'0',AURABOT_LOCK_MEMORY:this.options.lockMemory?'1':'0',AURABOT_SESSION_DATA_DIR:this.directory};
  }
  close():void {
    if(this.directory)rmSync(this.directory,{recursive:true,force:true});
  }
}
