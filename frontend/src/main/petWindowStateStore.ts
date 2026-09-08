/** Persists Pet window bounds by immutable Profile ID; legacy unowned bounds remain separate.
 * Main selects a key only after activation, preventing A's layout writes or restoration from
 * changing B. Atomic file replacement preserves the previous geometry on interrupted writes. */
import { existsSync, mkdirSync, readFileSync, writeFileSync, renameSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import type { WindowRectangle } from './windowPlacement';
const rectangle=z.object({x:z.number().finite(),y:z.number().finite(),width:z.number().finite().positive(),height:z.number().finite().positive()});
const schema=z.object({bounds:rectangle.optional(),profiles:z.record(z.string(),rectangle).default({})});
export class PetWindowStateStore {
 private state:z.infer<typeof schema>={profiles:{}};
 private readonly filePath:string;
 constructor(private readonly userDataPath:string){this.filePath=join(userDataPath,'pet-window-state.json');if(existsSync(this.filePath)){try{this.state=schema.parse(JSON.parse(readFileSync(this.filePath,'utf8')));}catch{/* Invalid legacy geometry is ignored, never applied to a different Profile. */}}}
 getBounds(profileId?:string):WindowRectangle|undefined{const bounds=profileId?this.state.profiles[profileId]:this.state.bounds;return bounds?{...bounds}:undefined;}
 saveBounds(bounds:WindowRectangle,profileId?:string):void{const safe=rectangle.parse(bounds);if(profileId)this.state.profiles[profileId]=safe;else this.state.bounds=safe;mkdirSync(this.userDataPath,{recursive:true});writeFileSync(this.filePath+'.tmp',JSON.stringify(this.state,null,2)+'\n','utf8');renameSync(this.filePath+'.tmp',this.filePath);}
}
