/** Installs a source checkout using Node/npm and uv without a platform-specific shell.
 * Runs frozen frontend/Python installs and prepares Electron native modules before staging
 * local Pi and verified speech assets, so first launch uses already-prepared dependencies.
 * The runtime installer checks the actual Python/Node/Pi paths before setup can report success.
 * Shares Electron's data-root selection; existing settings/Profile data are never rewritten.
 * Requires Node >=22.19 (native TypeScript loading) and uv on PATH; no global Pi installation.
 */
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {npmCommand} from './npm-command.mjs';

try {
 const {values}=parseArgs({options:{help:{type:'boolean',short:'h'}},allowPositionals:false});
 if(values.help){
  console.log('Usage: node frontend/scripts/setup-source.mjs\nRequires Node >=22.19 and uv. Optional: set AURABOT_USER_DATA_DIR before setup and launch.');
 }else{
  const [major,minor]=process.versions.node.split('.').map(Number);
  if(major<22||(major===22&&minor<19))throw new Error('Install Node.js >=22.19 before source setup.');
  const {installRuntime}=await import('./install-runtime.mjs');
  const {applicationDataRoot}=await import('../src/main/applicationPaths.ts');
  const cwd=fileURLToPath(new URL('../../',import.meta.url));
  const run=(command,args,directory=cwd)=>{
   const result=spawnSync(command,args,{cwd:directory,stdio:'inherit',windowsHide:true});
   if(result.error||result.status!==0)throw new Error(result.error?.message??`${command} failed (${result.status})`);
  };
  const root=applicationDataRoot();
  const [node,args]=npmCommand();
  run('uv',['--version']);
  console.log(`AuraBot data directory: ${root}`);
  const frontend=fileURLToPath(new URL('../',import.meta.url));
  run(node,[...args,'ci','--no-audit','--no-fund'],frontend);
  run(node,[...args,'run','rebuild:native'],frontend);
  run('uv',['sync','--project','backend','--frozen','--extra','speech','--python','3.12']);
  await installRuntime(root);
  console.log(`Source setup complete. Data: ${root}\nStart: npm --prefix frontend run dev\nKeep the same AURABOT_USER_DATA_DIR if you set one. On PowerShell, use npm.cmd.`);
 }
}catch(error){console.error(error.message);process.exitCode=1;}
