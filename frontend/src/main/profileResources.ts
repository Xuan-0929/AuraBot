/** Serves only repository-owned Live2D files through a secure standard Electron scheme.
 * The main process registers privileges before app readiness and installs the handler afterwards;
 * per-instance query strings isolate renderer texture caches without granting filesystem access.
 */
import { protocol, net } from 'electron';
import { pathToFileURL } from 'node:url';
import type { ProfileRepository } from '../core/profiles/profileRepository';
export const PROFILE_RESOURCE_SCHEME = 'aurabot-model';
export function registerProfileResourceScheme():void {
  protocol.registerSchemesAsPrivileged([{scheme:PROFILE_RESOURCE_SCHEME,privileges:{standard:true,secure:true,supportFetchAPI:true,corsEnabled:true,stream:true}}]);
}
export function installProfileResourceProtocol(repository:ProfileRepository):void {
  protocol.handle(PROFILE_RESOURCE_SCHEME,async request=>{
    try {
      if(request.method!=='GET'&&request.method!=='HEAD')return new Response(null,{status:405});
      // Inspect raw path before URL normalization, which otherwise erases literal dot segments.
      const raw=request.url.replace(/^[a-z0-9+.-]+:\/\/[^/]+\//i,'').split(/[?#]/,1)[0];
      if(raw.split('/').some(segment=>{const decoded=decodeURIComponent(segment);return decoded==='.'||decoded==='..'||decoded.includes('/')||decoded.includes('\\')||decoded.includes('%');}))return new Response(null,{status:403});
      const url=new URL(request.url);if(url.hostname!=='profile')return new Response(null,{status:403});
      const [id,version,...parts]=url.pathname.slice(1).split('/').map(decodeURIComponent);
      if(!id||!version||!parts.length)return new Response(null,{status:404});
      const path=repository.resolveResource(id,version,parts.join('/'));
      const response=await net.fetch(pathToFileURL(path).toString(),{method:request.method});
      const headers=new Headers(response.headers);headers.set('Access-Control-Allow-Origin','*');headers.set('Cache-Control','private, max-age=31536000, immutable');
      return new Response(response.body,{status:response.status,headers});
    }catch{return new Response('Model resource unavailable',{status:404});}
  });
}
