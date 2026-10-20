/** Prepares the same pinned models/licenses used by Electron, before source launch or packaging.
 * Downloads only missing/corrupt assets, verifies size and SHA-256 before atomic publication,
 * and never edits Profile paths. A packaging job can pass its resources staging directory as root.
 * Undici honors standard proxy environment variables; it loads only after source setup installs npm dependencies.
 */
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile,rename,rm} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const manifest=JSON.parse(await readFile(new URL('../src/main/speech-assets.json',import.meta.url),'utf8'));
export async function prepareSpeechAssets(root,{assets=manifest.assets,version=manifest.version,request}={}) {
 if(!request){
  const {fetch,EnvHttpProxyAgent}=await import('undici');
  const dispatcher=new EnvHttpProxyAgent();
  try{return await prepareSpeechAssets(root,{assets,version,request:(url,init)=>fetch(url,{...init,dispatcher})});}
  finally{await dispatcher.close();}
 }
 const directory=join(root,'speech-assets',version);
 await mkdir(directory,{recursive:true});
 for(const asset of assets){
  const target=join(directory,asset.file);
  const valid=bytes=>bytes.length===asset.size&&createHash('sha256').update(bytes).digest('hex')===asset.sha256;
  try{if(valid(await readFile(target)))continue;}catch(error){if(error.code!=='ENOENT')throw error;}
  console.log(`Preparing ${asset.file}`);
  const response=await request(asset.url,{signal:AbortSignal.timeout(120000)}).catch(error=>{
   throw new Error(`Download failed: ${asset.file} (${error.cause?.code??error.message})`);
  });
  if(!response.ok||!response.body)throw new Error(`Download failed: ${asset.file} (HTTP ${response.status})`);
  const chunks=[];let size=0;
  for await(const chunk of response.body){
   size+=chunk.length;
   if(size>asset.size)throw new Error(`Integrity check failed: ${asset.file}`);
   chunks.push(chunk);
  }
  const bytes=Buffer.concat(chunks);
  if(!valid(bytes))throw new Error(`Integrity check failed: ${asset.file}`);
  const temporary=target+'.part';
  try{await writeFile(temporary,bytes,{mode:0o600});await rename(temporary,target);}
  finally{await rm(temporary,{force:true});}
 }
 return directory;
}

if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
 try{
  if(process.argv.length!==3)throw new Error('Usage: node frontend/scripts/prepare-speech-assets.mjs <data-or-resources-directory>');
  console.log(`Speech assets ready: ${await prepareSpeechAssets(resolve(process.argv[2]))}`);
 }catch(error){console.error(error.message);process.exitCode=1;}
}
