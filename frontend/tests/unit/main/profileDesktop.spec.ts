/** Exercises per-Profile window ownership and rejects legacy persona as a continuing settings source.
 * All files are temporary; these checks protect isolation without starting Backend or media devices. */
import {mkdtempSync,rmSync,writeFileSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {afterEach,expect,it} from 'vitest';
import {PetWindowStateStore} from '../../../src/main/petWindowStateStore';
import {DesktopConfigStore} from '../../../src/main/desktopConfigStore';
const roots:string[]=[];
function root(){const result=mkdtempSync(join(tmpdir(),'aura-profile-desktop-'));roots.push(result);return result;}
afterEach(()=>roots.splice(0).forEach(path=>rmSync(path,{recursive:true,force:true})));
it('keeps A and B bounds independent across restart',()=>{const path=root();const store=new PetWindowStateStore(path);const a={x:2,y:3,width:300,height:400};store.saveBounds(a,'a');expect(store.getBounds('b')).toBeUndefined();store.saveBounds({x:20,y:30,width:400,height:500},'b');expect(new PetWindowStateStore(path).getBounds('a')).toEqual(a);});
it('does not overwrite legacy settings until Profile migration is committed',()=>{const path=root();const seed=new DesktopConfigStore(path,path);const old={...seed.snapshot(),version:1,persona:{name:'Legacy'},desktop:{...seed.snapshot().desktop,currentModelId:'hiyori'}};writeFileSync(seed.path,JSON.stringify(old));const store=new DesktopConfigStore(path,path);expect(JSON.parse(readFileSync(store.path,'utf8')).persona.name).toBe('Legacy');store.completeProfileMigration();const stored=JSON.parse(readFileSync(store.path,'utf8'));expect(stored.version).toBe(2);expect(stored).not.toHaveProperty('persona');expect(stored.desktop).not.toHaveProperty('currentModelId');});
