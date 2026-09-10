/** Exercises canonical SOUL files and optimistic saves through the repository using only temporary roots. */
import {afterEach,expect,it} from 'vitest';
import {mkdtempSync,readFileSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {ProfileRepository} from '../../../src/core/profiles/profileRepository';
const roots:string[]=[];
afterEach(()=>roots.splice(0).forEach(root=>rmSync(root,{recursive:true,force:true})));
async function setup(){const root=mkdtempSync(join(tmpdir(),'aurabot-soul-'));roots.push(root);const repo=new ProfileRepository({profilesRoot:root});await repo.initialize();return repo;}
it('keeps external SOUL edits pending and rejects stale saves without losing either version',async()=>{
 const repo=await setup(),a=repo.create('A'),id=a.saved.profile.id;
 repo.markApplied(id,a.saved.revision);const file=join(repo.root,id,'SOUL.md');
 writeFileSync(file,'# 外部编辑\nこんにちは\n');const changed=repo.get(id);
 expect(changed.saved.soulText).toBe('# 外部编辑\nこんにちは\n');expect(changed.saved.revision).toBe(a.saved.revision+1);
 expect(changed.applied?.soulText).toBe(a.saved.soulText);
 expect(()=>repo.save(id,{...a.saved,soulText:'page draft',expectedSoulFingerprint:a.saved.soulFingerprint})).toThrow('soul_conflict');
 expect(readFileSync(file,'utf8')).toBe(changed.saved.soulText);
 const saved=repo.save(id,{...changed.saved,soulText:'page draft',expectedSoulFingerprint:changed.saved.soulFingerprint});
 expect(saved.saved.soulText).toBe('page draft');expect(repo.get(id).saved.revision).toBe(saved.saved.revision);
 expect(saved.saved.profile.soul).toBeUndefined();
});
it('allows empty saved SOUL but rejects empty or oversized application without truncation',async()=>{
 const repo=await setup(),a=repo.create('A'),id=a.saved.profile.id;
 const saved=repo.save(id,{...a.saved,soulText:'',expectedSoulFingerprint:a.saved.soulFingerprint});
 expect(()=>repo.markApplied(id,saved.saved.revision)).toThrow('soul_empty');
 const large='日'.repeat(12000);writeFileSync(join(repo.root,id,'SOUL.md'),large);
 const external=repo.get(id);expect(external.saved.soulText).toBe(large);
 expect(()=>repo.markApplied(id,external.saved.revision)).toThrow('soul_too_large');
});
it('migrates saved and applied legacy files independently, preserves an existing SOUL and replays safely',async()=>{
 const {createDefaultProfile}=await import('../../../src/core/profiles/soulService');
 const repo=await setup(),a=repo.create('A'),id=a.saved.profile.id,dir=join(repo.root,id);
 const seed=createDefaultProfile();const old={...a.saved.profile,soul:{...seed.soul,profileId:id,name:'旧人格',persona:'saved private',principles:['all fields retained']}};
 const applied={...a.saved,profile:{...old,soul:{...old.soul,persona:'applied private'}}};delete (applied as any).soulText;
 writeFileSync(join(dir,'applied.json'),JSON.stringify(applied));writeFileSync(join(dir,'soul.json'),JSON.stringify(old.soul));
 writeFileSync(join(dir,'profile.json'),JSON.stringify({...a.saved.profile,revision:2,modelVersion:''}));writeFileSync(join(dir,'SOUL.md'),'external document wins\n');
 await repo.initialize();const migrated=repo.get(id);
 expect(migrated.saved.soulText).toBe('external document wins\n');expect(migrated.applied?.soulText).toContain('applied private');
 expect(readFileSync(join(dir,'.soul-v1-backup/soul.json'),'utf8')).toContain('all fields retained');
 await repo.initialize();expect(repo.get(id).saved.revision).toBe(migrated.saved.revision);
});
