/** Owns one sequential, cancellable installation of pinned speech assets beneath app data.
 * Streams network bytes to temporary files with incremental hashes, atomically publishes verified
 * assets and broadcasts bounded progress through Main IPC. No credentials or renderer paths are used. */
import {createHash} from 'node:crypto';
import {createReadStream} from 'node:fs';
import {mkdir,open,rename,rm} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import type {SpeechAssetsState} from '../shared/contracts/speechAssets';
import {SPEECH_ASSETS, type SpeechAsset, speechAssetPaths} from './speechAssetManifest';
export class SpeechAssets {
 private operation:Promise<SpeechAssetsState>|undefined;
 private controller:AbortController|undefined;
 private lastProgress=0;
 private state:SpeechAssetsState;
 constructor(private root:string,private changed:(state:SpeechAssetsState)=>void,private manifest:readonly SpeechAsset[]=SPEECH_ASSETS,private request:(url:string,init?:RequestInit)=>Promise<Response>=fetch){
  this.state={phase:'idle',received:0,total:manifest.reduce((n,a)=>n+a.size,0),assets:manifest.map(a=>({key:a.key,ready:false})),paths:speechAssetPaths(root)};
 }
 snapshot(){return structuredClone(this.state);}
 async status(){
  if(this.operation)return this.snapshot();
  for(const [index,asset] of this.manifest.entries()){const ready=await this.valid(asset);if(this.operation)return this.snapshot();this.state.assets[index].ready=ready;}
  if(this.state.phase==='ready'&&this.state.assets.some(a=>!a.ready))this.state.phase='idle';
  this.state.received=this.manifest.reduce((n,a,i)=>n+(this.state.assets[i].ready?a.size:0),0);
  return this.snapshot();
 }
 prepare():Promise<SpeechAssetsState>{
  if(this.operation)return this.operation;
  this.controller=new AbortController();
  this.operation=this.download(this.controller.signal).finally(()=>{this.operation=undefined;this.controller=undefined;});
  return this.operation;
 }
 async cancel(){this.controller?.abort();await this.operation;return this.snapshot();}
 private notify(force=false){if(force||Date.now()-this.lastProgress>=200){this.lastProgress=Date.now();this.changed(this.snapshot());}}
 private path(asset:SpeechAsset){return this.state.paths[asset.key]??join(this.root,asset.file);}
 private async valid(asset:SpeechAsset){
  try{const hash=createHash('sha256');let size=0;for await(const chunk of createReadStream(this.path(asset))){size+=chunk.length;hash.update(chunk);}return size===asset.size&&hash.digest('hex')===asset.sha256;}catch{return false;}
 }
 private async download(signal:AbortSignal){
  this.state={...this.state,phase:'downloading',error:undefined,received:0,current:undefined};this.notify(true);
  let failure:SpeechAssetsState['error']='filesystem';
  try{
   for(const [index,asset] of this.manifest.entries()){
    signal.throwIfAborted();this.state.current=asset.key;
    if(await this.valid(asset)){this.state.assets[index].ready=true;this.state.received+=asset.size;this.notify();continue;}
    this.state.assets[index].ready=false;
    const target=this.path(asset),temporary=target+'.part';
    failure='filesystem';await mkdir(dirname(target),{recursive:true});
    let file:Awaited<ReturnType<typeof open>>|undefined;
    try{
     failure='network';
     const response=await this.request(asset.url,{signal:AbortSignal.any([signal,AbortSignal.timeout(120000)])});
     if(!response.ok||!response.body)throw new Error('network');
     failure='filesystem';file=await open(temporary,'w',0o600);
     const hash=createHash('sha256');let size=0;
     const reader=response.body.getReader();
     try{while(true){failure='network';const {done,value}=await reader.read();if(done)break;
      signal.throwIfAborted();size+=value.length;failure='integrity';if(size>asset.size)throw new Error('integrity');
      hash.update(value);failure='filesystem';await file.writeFile(value);this.state.received+=value.length;this.notify();
     }}finally{await reader.cancel().catch(()=>undefined);reader.releaseLock();}
     failure='integrity';if(size!==asset.size||hash.digest('hex')!==asset.sha256)throw new Error('integrity');
     signal.throwIfAborted();failure='filesystem';await file.close();file=undefined;await rename(temporary,target);this.state.assets[index].ready=true;
    }finally{await file?.close();await rm(temporary,{force:true});}
   }
   this.state.phase='ready';this.state.current=undefined;
  }catch{this.state.phase=signal.aborted?'cancelled':'error';this.state.error=signal.aborted?undefined:failure;}
  this.notify(true);return this.snapshot();
 }
}
