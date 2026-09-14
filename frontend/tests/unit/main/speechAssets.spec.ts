/** Exercises sequential resource downloads against local HTTP, including integrity and retry.
 * Temporary roots isolate installed assets; tests cross the same manager interface as desktop IPC. */
import {createServer,type Server} from 'node:http';
import {mkdtemp,readFile,rm,readdir,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {afterEach,expect,it} from 'vitest';
import {SpeechAssets} from '../../../src/main/speechAssets';
let server:Server;let root:string;
afterEach(async()=>{server?.closeAllConnections();await new Promise<void>(r=>server?server.close(()=>r()):r());if(root)await rm(root,{recursive:true,force:true});});
async function setup(body='model',hashBody=body){
 root=await mkdtemp(join(tmpdir(),'speech-assets-'));let requests=0;
 server=createServer((_req,res)=>{requests++;res.end(body);});
 await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));
 const address=server.address() as {port:number};
 const manifest=[{key:'silero_model_path' as const,file:'model.onnx',url:`http://127.0.0.1:${address.port}/model`,size:Buffer.byteLength(body),sha256:createHash('sha256').update(hashBody).digest('hex')}];
 const manager=new SpeechAssets(root,()=>{},manifest);
 return {manager,requests:()=>requests};
}
it('downloads once, merges concurrent preparation and reuses verified files',async()=>{
 const {manager,requests}=await setup();
 const first=manager.prepare();expect(manager.prepare()).toBe(first);
 expect((await first).phase).toBe('ready');
 const state=await manager.status();expect(state.received).toBe(state.total);
 expect(await readFile(state.paths.silero_model_path,'utf8')).toBe('model');
 await manager.prepare();expect(requests()).toBe(1);
});
it('rejects corrupt downloads and removes partial files',async()=>{
 const {manager}=await setup('wrong','right');
 expect((await manager.prepare()).error).toBe('integrity');
 expect((await manager.status()).assets[0].ready).toBe(false);
 expect((await readdir(root,{recursive:true})).some(p=>p.endsWith('.part'))).toBe(false);
});

it('cancels a stalled response, then retries successfully',async()=>{
 const {manager}=await setup();server.removeAllListeners('request');
 let requested!:()=>void;const started=new Promise<void>(r=>requested=r);
 server.on('request',(_req,res)=>{res.writeHead(200);res.write('m');requested();});
 const job=manager.prepare();await started;await manager.cancel();
 expect((await job).phase).toBe('cancelled');
 server.removeAllListeners('request');server.on('request',(_req,res)=>res.end('model'));
 expect((await manager.prepare()).phase).toBe('ready');
});
it('reports network and filesystem failures without publishing assets',async()=>{
 const {manager}=await setup();server.removeAllListeners('request');server.on('request',(_req,res)=>res.destroy());
 expect((await manager.prepare()).error).toBe('network');
 server.removeAllListeners('request');server.on('request',(_req,res)=>res.end('model'));
 await rm(join(root,'speech-assets'),{recursive:true,force:true});
 await writeFile(join(root,'speech-assets'),'blocking file');
 expect((await manager.prepare()).error).toBe('filesystem');
});
