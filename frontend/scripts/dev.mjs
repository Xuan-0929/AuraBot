/** Validates AuraBot launch flags before rebuilding native dependencies and starting Electron/Vite.
 * Namespaced application switches bypass Electron's reserved --debug switch; the separator also
 * keeps them out of electron-vite's CLI handling. Electron Main
 * owns Backend processes and temporary memory data; this launcher only forwards arguments and signals. */
import { spawn } from 'node:child_process';
import { parseArgs } from 'node:util';
import { fileURLToPath } from 'node:url';

try {
  const {values} = parseArgs({options:{debug:{type:'boolean'}, 'lock-memory':{type:'boolean'}, help:{type:'boolean',short:'h'}}, allowPositionals:false});
  if (values.help) {
    console.log('Usage: npm run dev -- [--debug] [--lock-memory]\n  --debug        Open Live Debug and write diagnostic logs for this run.\n  --lock-memory  Use fixed benchmark memory and a fresh temporary Context.');
  } else {
    let child;
    let stopped=false;
    for(const signal of ['SIGINT','SIGTERM']) process.on(signal,()=>{stopped=true;child?.kill(signal);});
    const run=(command,args)=>new Promise((resolve,reject)=>{
      child=spawn(command,args,{cwd:new URL('../',import.meta.url),env:{...process.env,ELECTRON_RENDERER_URL:'http://127.0.0.1:6400'},stdio:'inherit'});
      child.once('error',reject);
      child.once('exit',(code,signal)=>resolve(code??(signal?1:0)));
    });
    const cli=path=>fileURLToPath(new URL('../node_modules/'+path,import.meta.url));
    const rebuilt=await run(process.execPath,[cli('@electron/rebuild/lib/cli.js'),'-f','-w','better-sqlite3']);
    process.exitCode=rebuilt;
    if(!rebuilt&&!stopped) process.exitCode=await run(process.execPath,[cli('electron-vite/bin/electron-vite.js'),'dev','--',
      ...(values.debug?['--aurabot-debug']:[]),...(values['lock-memory']?['--aurabot-lock-memory']:[])]);
  }
} catch(error) {
  console.error(error instanceof Error?error.message:String(error));
  process.exitCode=1;
}
