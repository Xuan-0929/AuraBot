/** Starts only Electron/Vite. Electron Main is the sole owner of the Python Backend child. */
import { spawn } from 'node:child_process';
const child=spawn('npx',['electron-vite','dev'],{cwd:new URL('../',import.meta.url),env:{...process.env,ELECTRON_RENDERER_URL:'http://127.0.0.1:6400'},stdio:'inherit'});
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>child.kill(signal));
child.on('exit',code=>{process.exitCode=code??0;});
