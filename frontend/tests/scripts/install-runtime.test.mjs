/** Checks source installer staging without downloads: package links stay within the selected root,
 * and repeated staging preserves user settings/Profile files. Shared platform defaults and model
 * integrity/retry behavior are checked without network access. npm owns dependency installation.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, existsSync, writeFileSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { prepareRuntime } from '../../scripts/install-runtime.mjs';
import { npmCommand } from '../../scripts/npm-command.mjs';
import { spawnSync } from 'node:child_process';
import {createHash} from 'node:crypto';
import {applicationDataRoot} from '../../src/main/applicationPaths.ts';
import {prepareSpeechAssets} from '../../scripts/prepare-speech-assets.mjs';

test('runs npm as JavaScript without a shell, including Windows layouts and metacharacter paths', () => {
  const root=mkdtempSync(join(tmpdir(),'aura npm 中文 & '));
  try {
    const cli=join(root,'node_modules/npm/bin/npm-cli.js');mkdirSync(dirname(cli),{recursive:true});
    writeFileSync(cli,'console.log(JSON.stringify(process.argv.slice(2)))');
    const [node,args]=npmCommand({npm_execpath:cli},process.execPath);
    const forwarded=['ci','--prefix',join(root,'data & more')];
    const result=spawnSync(node,[...args,...forwarded],{encoding:'utf8'});
    assert.equal(result.status,0,result.stderr);assert.deepEqual(JSON.parse(result.stdout),forwarded);
    assert.deepEqual(npmCommand({Path:root},join(root,'node.exe')),[join(root,'node.exe'),[cli]]);
  } finally {rmSync(root,{recursive:true,force:true});}
});

test('stages the pinned runtime and local extension in a source-independent installation', () => {
  const root = mkdtempSync(join(tmpdir(), 'aura-pi-install-'));
  try {
    writeFileSync(join(root, 'settings.json'), 'existing-settings');
    const packages = prepareRuntime(root);
    assert.deepEqual(packages, [join(root,'pi-aurabot-task'),join(root,'pi-runtime')]);
    const manifest = JSON.parse(readFileSync(join(root,'pi-runtime/package.json'),'utf8'));
    const lock = JSON.parse(readFileSync(join(root,'pi-runtime/package-lock.json'),'utf8'));
    const extension = JSON.parse(readFileSync(join(root,'pi-aurabot-task/package.json'),'utf8'));
    assert.equal(manifest.dependencies['@earendil-works/pi-coding-agent'],'0.84.2');
    assert.equal(lock.packages['node_modules/@earendil-works/pi-coding-agent'].version,'0.84.2');
    assert.equal(extension.peerDependencies['@earendil-works/pi-coding-agent'],'0.84.2');
    assert.equal(manifest.dependencies['@scpz24/pi-aurabot-task'], 'file:../pi-aurabot-task');
    assert.ok(existsSync(join(root,'pi-aurabot-task/aurabot-task/index.ts')));
    assert.ok(existsSync(join(root,'pi-aurabot-task/prompts/task-system.md')));
    assert.ok(existsSync(join(root,'pi-runtime/package-lock.json')));
    prepareRuntime(root);
    assert.equal(readFileSync(join(root,'settings.json'),'utf8'),'existing-settings');
  } finally { rmSync(root,{recursive:true,force:true}); }
});

test('uses LocalAppData on Windows, retains macOS defaults and honors explicit roots',()=>{
  const win={platform:'win32',home:'C:\\Users\\测试',env:{LOCALAPPDATA:'D:\\Local Data',APPDATA:'D:\\Roaming'}};
  assert.equal(applicationDataRoot(win),'D:\\Local Data\\AuraBot\\desktop-v2');
  assert.equal(applicationDataRoot({...win,env:{}}),'C:\\Users\\测试\\AppData\\Local\\AuraBot\\desktop-v2');
  assert.equal(applicationDataRoot({...win,env:{AURABOT_USER_DATA_DIR:'D:\\custom & data'}}),'D:\\custom & data');
  assert.equal(applicationDataRoot({platform:'darwin',home:'/Users/test',env:{}}),'/Users/test/Library/Application Support/AuraBot/desktop-v2');
  assert.equal(applicationDataRoot({platform:'darwin',home:'/Users/test',env:{},userData:'/custom/electron'}),'/custom/electron/desktop-v2');
});

test('source setup verifies and reuses assets, rejects corrupt responses and preserves settings',async()=>{
  const root=mkdtempSync(join(tmpdir(),'aura speech 中文 & '));
  const bytes=Buffer.from('verified model');
  const asset={file:'model.onnx',url:'https://unused.invalid/model',size:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
  let requests=0;
  const options={assets:[asset],version:'test',request:async()=>{requests++;return new Response(bytes);}};
  try{
    writeFileSync(join(root,'settings.json'),'preserve me');
    const directory=await prepareSpeechAssets(root,options);
    await prepareSpeechAssets(root,options);
    assert.equal(requests,1);
    writeFileSync(join(directory,asset.file),'bad');
    await assert.rejects(prepareSpeechAssets(root,{...options,request:async()=>new Response('wrong')}),/Integrity/);
    assert.equal(readFileSync(join(directory,asset.file),'utf8'),'bad');
    assert.equal(existsSync(join(directory,asset.file+'.part')),false);
    await prepareSpeechAssets(root,options);
    assert.deepEqual(readFileSync(join(directory,asset.file)),bytes);
    assert.equal(readFileSync(join(root,'settings.json'),'utf8'),'preserve me');
  }finally{rmSync(root,{recursive:true,force:true});}
});
