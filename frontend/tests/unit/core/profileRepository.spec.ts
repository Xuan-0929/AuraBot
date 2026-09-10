/** Exercises Profile file ownership, resource confinement and revision transactions using temporary data only. */
import { afterEach, describe, expect, it } from 'vitest';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, readFileSync, symlinkSync, truncateSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type {AvatarEntry} from '../../../src/shared/contracts/avatar';
import { ProfileRepository } from '../../../src/core/profiles/profileRepository';

const roots:string[]=[];
function root(){const path=mkdtempSync(join(tmpdir(),'aurabot-profile-'));roots.push(path);return path;}
afterEach(()=>roots.splice(0).forEach(path=>rmSync(path,{recursive:true,force:true})));
async function repository(){const repo=new ProfileRepository({profilesRoot:join(root(),'profiles')});await repo.initialize();return repo;}
function model(){const path=root();mkdirSync(join(path,'动作'));writeFileSync(join(path,'测试.model3.json'),JSON.stringify({Version:3,FileReferences:{Moc:'test.moc3',Textures:[],Motions:{Idle:[{File:'动作/点头.motion3.json'}]}}}));writeFileSync(join(path,'test.moc3'),'fixture');writeFileSync(join(path,'动作/点头.motion3.json'),JSON.stringify({Version:3,Meta:{Duration:1},Curves:[]}));return path;}
describe('ProfileRepository',()=>{
 it('owns stable identities and keeps saved changes separate from applied',async()=>{const repo=await repository();const a=repo.create('A');const b=repo.create('B');expect(a.saved.profile.id).not.toBe(b.saved.profile.id);repo.markApplied(a.saved.profile.id,1);const changed=structuredClone(a.saved.profile);changed.displayName='Renamed';const saved=repo.save(changed.id,{profile:changed,adaptor:a.saved.adaptor});expect(saved.saved.revision).toBe(2);expect(saved.applied?.profile.displayName).toBe('A');expect(repo.save(changed.id,{profile:changed,adaptor:saved.saved.adaptor}).saved.revision).toBe(2);expect(repo.get(b.saved.profile.id).saved.profile.displayName).toBe('B');});
 it('copies model packages independently and validates references and semantics',async()=>{const repo=await repository();const a=repo.create('A');const b=repo.create('B');const source=model();const ai=repo.importModel(a.saved.profile.id,source,'测试.model3.json');const bi=repo.importModel(b.saved.profile.id,source,'测试.model3.json');rmSync(source,{recursive:true});expect(readFileSync(repo.resolveResource(ai.saved.profile.id,ai.saved.modelVersion,'test.moc3'),'utf8')).toBe('fixture');expect(repo.resolveResource(ai.saved.profile.id,ai.saved.modelVersion,'test.moc3')).not.toBe(repo.resolveResource(bi.saved.profile.id,bi.saved.modelVersion,'test.moc3'));expect(()=>repo.resolveResource(ai.saved.profile.id,ai.saved.modelVersion,'../profile.json')).toThrow();const adaptor=structuredClone(ai.saved.adaptor);adaptor.entries.nod={kind:'motion',source:{file:'model/动作/点头.motion3.json'},description:'用户预览后确认',agent_enabled:true,playback:{mode:'once'}};expect(()=>repo.save(ai.saved.profile.id,{profile:ai.saved.profile,adaptor})).toThrow('resource_unverified');repo.verifyResources(ai.saved.profile.id,ai.saved.modelVersion,['model/动作/点头.motion3.json']);expect(repo.save(ai.saved.profile.id,{profile:ai.saved.profile,adaptor}).saved.adaptor.entries.nod).toBeDefined();});
 it('rejects import symlinks and keeps prior model on failure',async()=>{const repo=await repository();const a=repo.create('A');const source=model();symlinkSync('/etc/passwd',join(source,'escape'));expect(()=>repo.importModel(a.saved.profile.id,source,'测试.model3.json')).toThrow('invalid_resource');expect(repo.get(a.saved.profile.id).saved.modelVersion).toBe('');});
 it('protects selected/last profiles and respects backend ownership refusal',async()=>{const repo=await repository();const id=repo.selectedId();expect(()=>repo.delete(id,{allowed:true})).toThrow();const b=repo.create('B');expect(()=>repo.delete(b.saved.profile.id,{allowed:false,reason:'归档尚未完成'})).toThrow('归档尚未完成');repo.delete(b.saved.profile.id,{allowed:true});expect(repo.list()).toHaveLength(1);});
});

describe('legacy migration',()=>{
 it('backs up WAL contents and retains complete Soul plus saved persona as pending',async()=>{
   const {ProfileStorage}=await import('../../../src/core/profiles/profileStorage');
   const Database=(await import('better-sqlite3')).default;
   const directory=root();const legacy=join(directory,'legacy.sqlite3');const storage=new ProfileStorage(legacy);const old=storage.getOrSeedActiveProfile();storage.close();
   const open=new Database(legacy);open.pragma('journal_mode = WAL');open.prepare('update souls set boundaries_json = ? where profile_id = ?').run(JSON.stringify(['private boundary']),old.id);
   const settings=join(directory,'settings.json');writeFileSync(settings,JSON.stringify({persona:{name:'Saved name',shortPersonaDescription:'Saved persona',tone:'warm',relationshipStyle:'friend',proactiveLevel:'balanced',principles:['p'],taboos:[],reminderStyle:'',replyPreference:''}}));
   const repo=new ProfileRepository({profilesRoot:join(directory,'profiles'),legacyDatabasePath:legacy,legacySettingsPath:settings});await repo.initialize();
   expect(repo.selectedId()).toBe(old.id);const detail=repo.get(old.id);expect(detail.saved.soulText).toContain('private boundary');expect(detail.saved.profile.displayName).toBe('Saved name');expect(detail.applied?.profile.displayName).toBe(old.displayName);expect(detail.saved.revision).toBe(2);
   await repo.initialize();expect(repo.list()).toHaveLength(1);expect(repo.get(old.id).saved.revision).toBe(2);
   const backup=new Database(join(directory,'profiles/.legacy-backup/profiles.sqlite3'),{readonly:true});expect((backup.prepare('select boundaries_json from souls').get() as {boundaries_json:string}).boundaries_json).toContain('private boundary');backup.close();open.close();
 });
 it('restores an interrupted save transaction without resetting memory files',async()=>{
   const repo=await repository();const detail=repo.create('A');const directory=join(repo.root,detail.saved.profile.id);mkdirSync(join(directory,'memory'));writeFileSync(join(directory,'memory/keep'),'private');
   const config=structuredClone(detail.saved);config.profile.displayName='Recovered';config.revision=2;config.adaptor.revision=2;writeFileSync(join(directory,'.save-journal.json'),JSON.stringify(config));
   expect(repo.get(config.profile.id).saved.profile.displayName).toBe('Recovered');expect(readFileSync(join(directory,'memory/keep'),'utf8')).toBe('private');
 });
});

describe('capability boundaries',()=>{
 it('rejects reserved names, wrong playback modes, runtime loops exposed to Agent and unsafe paths',async()=>{
   const repo=await repository();const profile=repo.create('A');const imported=repo.importModel(profile.saved.profile.id,model(),'测试.model3.json');const file='model/动作/点头.motion3.json';repo.verifyResources(imported.saved.profile.id,imported.saved.modelVersion,[file]);
   const entry={kind:'motion' as const,source:{file},description:'confirmed',agent_enabled:true,playback:{mode:'once' as const}};
   for(const entries of ([{neutral:entry},{nod:{...entry,playback:{mode:'hold' as const}}},{nod:{...entry,playback:{mode:'loop' as const}}}] as Record<string,AvatarEntry>[]))expect(()=>repo.save(imported.saved.profile.id,{profile:imported.saved.profile,adaptor:{...imported.saved.adaptor,entries}})).toThrow();
   for(const path of ['../profile.json','%2e%2e/profile.json','/etc/passwd','https://invalid/a','model/动作/../../profile.json'])expect(()=>repo.resolveResource(imported.saved.profile.id,imported.saved.modelVersion,path)).toThrow();
   const saved=repo.save(imported.saved.profile.id,{profile:imported.saved.profile,adaptor:{...imported.saved.adaptor,entries:{nod:entry},validated_resources:['forged']}});expect(saved.saved.adaptor.validated_resources).toEqual([file]);
 });
});

describe('Profile destination ownership and visual degradation',()=>{
 it('rejects nested import destination symlinks without writing outside the Profile',async()=>{
   const repo=await repository();const a=repo.create('A'),id=a.saved.profile.id;const outside=root();const live2d=join(repo.root,id,'live2d');
   symlinkSync(outside,join(live2d,'packages'),'dir');
   expect(()=>repo.importModel(id,model(),'测试.model3.json')).toThrow('invalid_resource');
   expect((await import('node:fs')).readdirSync(outside)).toEqual([]);
   expect(repo.get(id).saved.modelVersion).toBe('');
 });
 it('refuses symlinked configuration and temporary files before touching external contents',async()=>{
   const repo=await repository();const detail=repo.create('A'),id=detail.saved.profile.id;const outside=join(root(),'soul.json');writeFileSync(outside,'untouched');
   symlinkSync(outside,join(repo.root,id,'SOUL.md.tmp'));
   const changed=structuredClone(detail.saved.profile);changed.displayName='Changed';
   expect(()=>repo.save(id,{profile:changed,adaptor:detail.saved.adaptor})).toThrow('invalid_resource');
   expect(readFileSync(outside,'utf8')).toBe('untouched');
   expect(repo.get(id).saved.profile.displayName).toBe('A');
 });
 it('rejects a symlinked Soul file instead of reading or replacing another file',async()=>{
   const repo=await repository();const detail=repo.create('A'),id=detail.saved.profile.id;const external=join(root(),'private.json');writeFileSync(external,detail.saved.soulText);
   rmSync(join(repo.root,id,'SOUL.md'));symlinkSync(external,join(repo.root,id,'SOUL.md'));
   expect(()=>repo.get(id)).toThrow('invalid_resource');expect(()=>repo.save(id,{profile:detail.saved.profile,adaptor:detail.saved.adaptor})).toThrow('invalid_resource');
   expect(readFileSync(external,'utf8')).toBe(detail.saved.soulText);
 });
 it('rejects symlinked Live2D configuration ancestors before committing Soul',async()=>{
   const repo=await repository();const detail=repo.create('A'),id=detail.saved.profile.id;const outside=root();
   writeFileSync(join(outside,'adaptor.json'),JSON.stringify(detail.saved.adaptor));rmSync(join(repo.root,id,'live2d'),{recursive:true});symlinkSync(outside,join(repo.root,id,'live2d'),'dir');
   const changed=structuredClone(detail.saved.profile);changed.displayName='Changed';
   expect(()=>repo.save(id,{profile:changed,adaptor:detail.saved.adaptor})).toThrow('invalid_resource');
   expect(JSON.parse(readFileSync(join(repo.root,id,'profile.json'),'utf8')).displayName).toBe('A');
   expect((await import('node:fs')).readdirSync(outside)).toEqual(['adaptor.json']);
 });
 it('saves complete Soul despite missing model resources without altering its adaptor or old applied snapshot',async()=>{
   const repo=await repository();const a=repo.create('A');const imported=repo.importModel(a.saved.profile.id,model(),'测试.model3.json');const id=imported.saved.profile.id;repo.markApplied(id,imported.saved.revision);
   rmSync(repo.resolveResource(id,imported.saved.modelVersion,'model/test.moc3'));
   const changed=structuredClone(imported.saved.profile);changed.displayName='Renamed';
   const saved=repo.save(id,{profile:changed,soulText:'Saved with unavailable visual',expectedSoulFingerprint:imported.saved.soulFingerprint,adaptor:imported.saved.adaptor});
   expect(saved.saved.soulText).toBe('Saved with unavailable visual');expect(saved.saved.adaptor.entries).toEqual(imported.saved.adaptor.entries);expect(saved.applied?.profile.displayName).toBe('A');
   expect(repo.list().find(p=>p.id===id)?.visualStatus).toBe('invalid');
   expect(repo.save(id,{profile:saved.saved.profile,adaptor:saved.saved.adaptor}).saved.revision).toBe(saved.saved.revision);
   expect(repo.markApplied(id,saved.saved.revision).applied?.soulText).toBe('Saved with unavailable visual');
   expect(repo.list().find(p=>p.id===id)?.visualStatus).toBe('invalid');
 });
});

it('bounds model JSON before parsing or exposing it as an available resource',async()=>{
 const repo=await repository();const a=repo.create('A'),source=model();
 writeFileSync(join(source,'unlisted.motion3.json'),'{}');truncateSync(join(source,'unlisted.motion3.json'),4*1024*1024+1);
 const imported=repo.importModel(a.saved.profile.id,source,'测试.model3.json');
 expect(imported.resources.find(r=>r.file==='model/unlisted.motion3.json')?.error).toBe('import_failed');
 truncateSync(join(source,'测试.model3.json'),4*1024*1024+1);
 expect(()=>repo.importModel(a.saved.profile.id,source,'测试.model3.json')).toThrow('import_failed');
});

it('migrates saved/applied single bindings independently with backups and no revision or Soul changes',async()=>{
 const repo=await repository(),detail=repo.create('Old');const id=detail.saved.profile.id,directory=join(repo.root,id);
 const legacy={...detail.saved.adaptor,schema_version:1,entries:{old:{kind:'motion',source:{file:'model/old.motion3.json'},description:'kept',agent_enabled:false,playback:{mode:'loop'}}},states:{idle:'old',listening:null,thinking:null,speaking:null}};
 writeFileSync(join(directory,'live2d/adaptor.json'),JSON.stringify(legacy));
 writeFileSync(join(directory,'applied.json'),JSON.stringify({...detail.saved,revision:7,adaptor:{...legacy,revision:7,states:{...legacy.states,idle:null}},soulText:'previous applied SOUL'}));
 const migrated=repo.get(id);expect(migrated.saved.adaptor.schema_version).toBe(2);expect(migrated.saved.adaptor.states.idle).toEqual(['old']);expect(migrated.saved.revision).toBe(detail.saved.revision);expect(migrated.saved.soulText).toBe(detail.saved.soulText);expect(migrated.applied?.adaptor.states.idle).toEqual([]);expect(migrated.applied?.soulText).toBe('previous applied SOUL');
 expect(JSON.parse(readFileSync(join(directory,'live2d/adaptor.json.v1-backup'),'utf8'))).toEqual(legacy);expect(JSON.parse(readFileSync(join(directory,'applied.json.v1-backup'),'utf8')).revision).toBe(7);expect(repo.get(id)).toEqual(migrated);
});
it('accepts verified mixed resident pools and empty descriptions but rejects duplicate, over-limit and once candidates',async()=>{
 const repo=await repository(),profile=repo.create('Mixed'),source=model();writeFileSync(join(source,'face.exp3.json'),JSON.stringify({Parameters:[]}));const imported=repo.importModel(profile.saved.profile.id,source,'测试.model3.json');const id=imported.saved.profile.id,file='model/动作/点头.motion3.json';repo.verifyResources(id,imported.saved.modelVersion,[file,'model/face.exp3.json']);
 const adaptor=structuredClone(imported.saved.adaptor);adaptor.entries.body={kind:'motion',source:{file},description:'',agent_enabled:false,playback:{mode:'loop'}};adaptor.entries.face={kind:'expression',source:{file:'model/face.exp3.json'},description:'',agent_enabled:true,playback:{mode:'hold'}};adaptor.states.idle=['body','face'];adaptor.states.thinking=['face'];
 const saved=repo.save(id,{profile:imported.saved.profile,adaptor});expect(saved.saved.adaptor.states).toEqual(adaptor.states);expect(saved.saved.adaptor.entries.face!.description).toBe('');
 for(const pool of [['body','body'],['missing'],Array.from({length:9},()=> 'body')])expect(()=>repo.save(id,{profile:saved.saved.profile,adaptor:{...adaptor,states:{...adaptor.states,idle:pool}}})).toThrow();
 adaptor.entries.body!.playback.mode='once';expect(()=>repo.save(id,{profile:saved.saved.profile,adaptor})).toThrow('invalid_adaptor');
});
