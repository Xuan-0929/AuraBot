/** Resolves the persistent data root shared by source setup and Electron Main.
 * Explicit environment overrides win; Windows uses LocalAppData instead of roaming storage.
 * Other platforms retain Electron's userData default; this only selects paths and never migrates files.
 */
import {homedir} from 'node:os';
import {posix,win32} from 'node:path';

export function applicationDataRoot({env=process.env,platform=process.platform,home=homedir(),userData}: {
 env?:NodeJS.ProcessEnv;platform?:NodeJS.Platform;home?:string;userData?:string;
}={}) {
 const path=platform==='win32'?win32:posix;
 if(env.AURABOT_USER_DATA_DIR)return path.resolve(env.AURABOT_USER_DATA_DIR);
 const base=platform==='win32'
  ? path.join(env.LOCALAPPDATA||path.join(home,'AppData','Local'),'AuraBot')
  : userData??(platform==='darwin'?path.join(home,'Library','Application Support','AuraBot'):path.join(env.XDG_CONFIG_HOME||path.join(home,'.config'),'AuraBot'));
 return path.join(base,'desktop-v2');
}
