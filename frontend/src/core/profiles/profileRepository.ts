/** Owns Profile configuration files and model packages inside the configured shared profiles root.
 * Validates and persists bounded Profile-local gaze and speech-mouth settings and framing preference alongside the adaptor.
 * Profile-local voice settings commit with identity and revision; renderer details redact all saved/applied secrets.
 * New identities receive a concise named SOUL template; legacy personas retain their existing text.
 * SOUL.md is the only editable persona; fingerprints detect external changes.
 * A replayable journal commits SOUL/adaptor/identity together; immutable applied snapshots isolate
 * running conversations from edits. Legacy SQLite is backed up consistently before import. Context,
 * Memory and Qdrant remain Backend-owned: this repository only reports their paths and existence.
 */
import type {SpeechAssetPaths} from '../../shared/contracts/speechAssets';
import {defaultProfileVoice,profileVoiceSchema,voiceSecretPaths,voiceSecret} from '../../shared/contracts/voice';
import Database from 'better-sqlite3';
import { closeSync, copyFileSync, existsSync, lstatSync, mkdirSync, openSync, readFileSync, readSync, readdirSync, realpathSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { z } from 'zod';
import { profileSchema, type Profile } from '../../shared/schemas/profile';
import { AVATAR_LIMITS, emptyAvatarAdaptor, upgradeAvatarAdaptor, type AvatarAdaptor, type AvatarResource } from '../../shared/contracts/avatar';
import type { ProfileConfig, ProfileDeleteGuard, ProfileDetail, ProfileSaveInput, ProfileSummary } from '../../shared/contracts/profiles';
import type { PersonaSettings } from '../../shared/contracts/personaSettings';
import { createDefaultProfile } from './soulService';
import { legacySoulMarkdown, defaultSoulMarkdown, validateSoul, SOUL_MAX_BYTES } from './soulPrompt';
import { mapProfile } from './profileStorage';

const ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const VERSION = /^[a-f0-9]{64}$/;
const NAME = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const stateNames = ['idle', 'listening', 'thinking', 'speaking'] as const;
const finiteFade = z.number().finite().min(0).max(AVATAR_LIMITS.fadeMs).optional();
const adaptorSchema = z.object({
  schema_version: z.literal(2), revision: z.number().int().positive(),
  model: z.object({manifest:z.string()}).strict().nullable(),
  validated_resources:z.array(z.string()).optional(),
  show_full_model:z.boolean().optional(),
  gaze_sensitivity:z.number().finite().min(0).max(AVATAR_LIMITS.gazeSensitivity).optional(),
  lip_sensitivity:z.number().finite().min(0).max(AVATAR_LIMITS.lipSensitivity).optional(),
  entries:z.record(z.string(),z.object({kind:z.enum(['motion','expression']),display_name:z.string().min(1).max(128).optional(),source:z.object({file:z.string()}).strict(),description:z.string().max(AVATAR_LIMITS.descriptionLength),agent_enabled:z.boolean(),playback:z.object({mode:z.enum(['once','loop','hold']),fade_in_ms:finiteFade,fade_out_ms:finiteFade}).strict()}).strict()),
  states:z.object({idle:z.array(z.string()).max(AVATAR_LIMITS.stateEntries),listening:z.array(z.string()).max(AVATAR_LIMITS.stateEntries),thinking:z.array(z.string()).max(AVATAR_LIMITS.stateEntries),speaking:z.array(z.string()).max(AVATAR_LIMITS.stateEntries)}).strict()
}).strict();
function json<T>(path:string):T{return JSON.parse(readFileSync(path,'utf8')) as T;}
function resourceJson(path:string):any{if(lstatSync(path).size>4*1024*1024)throw new Error('import_failed');return json(path);}
function atomic(path:string,value:unknown):void{mkdirSync(dirname(path),{recursive:true});const tmp=path+'.tmp';writeFileSync(tmp,JSON.stringify(value,null,2)+'\n',{mode:0o600});renameSync(tmp,path);}
function checkedId(id:string):string{if(!ID.test(id))throw new Error('invalid_resource');return id;}
function resourcePath(file:string):string {
  if(!file || /[\\\x00-\x1f?#]/.test(file) || file.includes('%') || isAbsolute(file) || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(file))throw new Error('invalid_resource');
  if(file.split('/').some(part=>!part || part==='.' || part==='..'))throw new Error('invalid_resource');
  return file;
}
function confined(root:string,file:string):string {
  resourcePath(file);const path=resolve(root,file);const base=realpathSync(root);
  if(!existsSync(path))throw new Error('invalid_resource');
  const actual=realpathSync(path);if(actual!==base&&!actual.startsWith(base+sep))throw new Error('invalid_resource');
  for(let current=path;current!==root;current=dirname(current)){if(lstatSync(current).isSymbolicLink())throw new Error('invalid_resource');}
  if(!lstatSync(actual).isFile())throw new Error('invalid_resource');return actual;
}
function walk(root:string):string[]{
  if(lstatSync(root).isSymbolicLink())throw new Error('invalid_resource');
  const files:string[]=[];let bytes=0;
  const visit=(dir:string)=>{for(const name of readdirSync(dir).sort()){const path=join(dir,name);const stat=lstatSync(path);if(stat.isSymbolicLink())throw new Error('invalid_resource');if(stat.isDirectory())visit(path);else if(stat.isFile()){bytes+=stat.size;if(bytes>1024*1024*1024 || files.length>=10000)throw new Error('entry_limit');files.push(relative(root,path).split(sep).join('/'));}else throw new Error('import_failed');}};
  visit(root);return files;
}
export function findModelManifests(folder:string):string[]{return walk(resolve(folder)).filter(file=>file.endsWith('.model3.json'));}
function manifestReferences(root:string,manifest:string):Map<string,'motion'|'expression'> {
  const value=resourceJson(confined(root,manifest));if(value.Version!==3 || !value.FileReferences || typeof value.FileReferences.Moc!=='string')throw new Error('import_failed');
  const refs=value.FileReferences;const base=dirname(manifest);const resources=new Map<string,'motion'|'expression'>();
  const add=(file:unknown,kind?:'motion'|'expression')=>{if(typeof file!=='string')throw new Error('import_failed');resourcePath(file);const relativeFile=base==='.'?file:`${base}/${file}`;confined(root,relativeFile);if(kind)resources.set(relativeFile,kind);};
  add(refs.Moc);if(!Array.isArray(refs.Textures))throw new Error('import_failed');refs.Textures.forEach((file:unknown)=>add(file));
  for(const key of ['Physics','Pose','DisplayInfo','UserData'])if(refs[key])add(refs[key]);
  if(refs.Motions)for(const motions of Object.values(refs.Motions)){if(!Array.isArray(motions))throw new Error('invalid_adaptor');motions.forEach(m=>add(m.File,'motion'));}
  if(refs.Expressions){if(!Array.isArray(refs.Expressions))throw new Error('invalid_adaptor');refs.Expressions.forEach((e:any)=>add(e.File,'expression'));}
  return resources;
}
export interface ProfileRepositoryOptions {profilesRoot:string;legacyDatabasePath?:string;legacySettingsPath?:string;speechAssetPaths?:SpeechAssetPaths}
export class ProfileRepository {
  readonly root:string;
  /** Check every existing repository-owned path segment, including dangling links and .tmp files. */
  private owned(path:string):string {
    const absolute=resolve(path),part=relative(this.root,absolute);
    if(part==='..'||part.startsWith('..'+sep)||isAbsolute(part))throw new Error('invalid_resource');
    let current=this.root;
    for(const segment of ['',...part.split(sep).filter(Boolean)]){
      if(segment)current=join(current,segment);
      try{if(lstatSync(current).isSymbolicLink())throw new Error('invalid_resource');}
      catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}
    }
    return absolute;
  }
  private json<T>(path:string):T{return json<T>(this.owned(path));}
  private atomic(path:string,value:unknown):void{this.owned(path);this.owned(path+'.tmp');atomic(path,value);}
  private checkConfigWrites(id:string):void{
    for(const file of ['profile.json','SOUL.md','live2d/adaptor.json','.save-journal.json']){
      const path=join(this.directory(id),file);this.owned(path);this.owned(path+'.tmp');
    }
  }
  constructor(private readonly options:ProfileRepositoryOptions){this.root=resolve(options.profilesRoot);}
  private directory(id:string):string{return this.owned(join(this.root,checkedId(id)));}
  async initialize():Promise<void>{
    this.owned(this.root);mkdirSync(this.root,{recursive:true});
    if(!existsSync(join(this.root,'.migration-v1.json'))){await this.migrateLegacy();this.atomic(join(this.root,'.migration-v1.json'),{version:1,completedAt:new Date().toISOString()});}
    if(!this.ids().length){const seed=createDefaultProfile();this.write(this.canonical({profile:seed.profile,revision:1,adaptor:emptyAvatarAdaptor(),modelVersion:'',soulText:defaultSoulMarkdown('zh')}));}
    for(const id of this.ids()){this.read(id);this.applied(id);}
    if(!this.selectedId())this.select(this.ids()[0]);
  }
  private ids():string[]{return readdirSync(this.root).filter(id=>ID.test(id)&&existsSync(join(this.directory(id),'profile.json')));}
  selectedId():string{const path=join(this.root,'index.json');if(!existsSync(path))return '';const id=this.json<{selectedId:string}>(path).selectedId;return ID.test(id)&&existsSync(join(this.directory(id),'profile.json'))?id:'';}
  select(id:string):void{this.get(id);this.atomic(join(this.root,'index.json'),{selectedId:id});}
  private recover(id:string):void{const journal=join(this.directory(id),'.save-journal.json');if(existsSync(journal)){const config=this.json<ProfileConfig>(journal);if(config.profile.id!==id)throw new Error('invalid_resource');if(config.adaptor.schema_version!==2)this.backupAdaptor(journal);this.commit(config);}}
  private fingerprint(text:string):string{return createHash('sha256').update(text).digest('hex');}
  private defaultVoice(){const voice=defaultProfileVoice();Object.assign(voice.speech,this.options.speechAssetPaths);return voice;}
  private canonical(config:Omit<ProfileConfig,'soulText'|'soulFingerprint'|'voice'> & {soulText?:string;voice?:ProfileConfig['voice']}):ProfileConfig {
    const {soul,...profile}=config.profile;const soulText=config.soulText??legacySoulMarkdown(soul,profile.displayName);
    return {...config,voice:profileVoiceSchema.parse(config.voice??this.defaultVoice()),adaptor:adaptorSchema.parse(upgradeAvatarAdaptor(config.adaptor)),profile,soulText,soulFingerprint:this.fingerprint(soulText)};
  }
  private commit(input:ProfileConfig):void {
    const oldPath=join(this.directory(input.profile.id),'live2d/adaptor.json');if(existsSync(oldPath)&&this.json<AvatarAdaptor>(oldPath).schema_version!==2)this.backupAdaptor(oldPath);
    const config=this.canonical(input);this.checkConfigWrites(config.profile.id);const directory=this.directory(config.profile.id);
    const path=this.soulPath(config.profile.id);mkdirSync(directory,{recursive:true});writeFileSync(path+'.tmp',config.soulText,{mode:0o600});renameSync(path+'.tmp',path);
    this.atomic(join(directory,'live2d/adaptor.json'),config.adaptor);
    this.atomic(join(directory,'profile.json'),{...config.profile,voice:config.voice,soulFormat:2,soulFingerprint:config.soulFingerprint,revision:config.revision,modelVersion:config.modelVersion});
    rmSync(join(directory,'.save-journal.json'),{force:true});
  }
  private write(config:ProfileConfig):void{this.checkConfigWrites(config.profile.id);this.atomic(join(this.directory(config.profile.id),'.save-journal.json'),config);this.commit(config);}
  soulPath(id:string):string{return this.owned(join(this.directory(id),'SOUL.md'));}
  private backupSoulFiles(id:string):void {
    const directory=this.directory(id),backup=this.owned(join(directory,'.soul-v1-backup'));mkdirSync(backup,{recursive:true});
    for(const file of ['profile.json','soul.json','applied.json','.save-journal.json']){const source=this.owned(join(directory,file)),target=this.owned(join(backup,file));if(existsSync(source)&&!existsSync(target))copyFileSync(source,target);}
  }
  private backupAdaptor(path:string):void {
    const backup=this.owned(path+'.v1-backup');if(existsSync(path)&&!existsSync(backup))copyFileSync(this.owned(path),backup);
  }
  private readAdaptor(path:string):AvatarAdaptor {
    const original=this.json<AvatarAdaptor>(path),converted=adaptorSchema.parse(upgradeAvatarAdaptor(original));
    if(original.schema_version!==2){this.backupAdaptor(path);this.atomic(path,converted);}return converted;
  }
  private read(id:string):ProfileConfig {
    const directory=this.directory(id);
    const before=this.json<any>(join(directory,'profile.json'));
    if(before.soulFormat!==2)this.backupSoulFiles(id);
    // An old pending journal is itself a recoverable saved configuration, not an applied snapshot.
    const journal=join(directory,'.save-journal.json');
    if(existsSync(journal)&&before.soulFormat!==2){const pending=this.json<ProfileConfig>(journal);if(pending.profile.id!==id)throw new Error('invalid_profile_id');const config=this.canonical(pending);if(existsSync(this.soulPath(id)))config.soulText=readFileSync(this.soulPath(id),'utf8');this.commit(config);}
    else this.recover(id);
    const {revision,modelVersion,soulFormat,soulFingerprint,voice:storedVoice,...identity}=this.json<any>(join(directory,'profile.json'));
    if(soulFormat!==2){
      const legacyPath=this.owned(join(directory,'soul.json'));const soul=existsSync(legacyPath)?this.json<Profile['soul']>(legacyPath):identity.soul;
      const config=this.canonical({profile:{...identity,soul},revision,modelVersion,adaptor:this.json(join(directory,'live2d/adaptor.json')),soulText:existsSync(this.soulPath(id))?readFileSync(this.soulPath(id),'utf8'):undefined});
      if(existsSync(this.soulPath(id))&&config.soulText!==legacySoulMarkdown(soul,config.profile.displayName)){config.revision++;config.adaptor.revision=config.revision;}
      this.write(config);return config;
    }
    const soulText=readFileSync(this.soulPath(id),'utf8'),fingerprint=this.fingerprint(soulText);
    const changed=fingerprint!==soulFingerprint;const nextRevision=revision+Number(changed);
    const adaptor=this.readAdaptor(join(directory,'live2d/adaptor.json'));
    if(changed){adaptor.revision=nextRevision;this.atomic(join(directory,'live2d/adaptor.json'),adaptor);this.atomic(join(directory,'profile.json'),{...identity,voice:storedVoice,soulFormat:2,soulFingerprint:fingerprint,revision:nextRevision,modelVersion});}
    return {profile:identity,voice:profileVoiceSchema.parse(storedVoice??this.defaultVoice()),soulText,soulFingerprint:fingerprint,revision:nextRevision,modelVersion,adaptor};
  }
  applied(id:string):ProfileConfig|null {
    const path=join(this.directory(id),'applied.json');if(!existsSync(path))return null;
    const original=this.json<ProfileConfig>(path);const adaptor=adaptorSchema.parse(upgradeAvatarAdaptor(original.adaptor));if(original.adaptor.schema_version!==2){this.backupAdaptor(path);original.adaptor=adaptor;this.atomic(path,original);}if(typeof original.soulText==='string')return this.canonical(original);
    this.backupSoulFiles(id);const converted=this.canonical(original);this.atomic(path,converted);return converted;
  }
  get(id:string):ProfileDetail{const saved=this.read(id);let resources:AvatarResource[]=[];if(saved.modelVersion){try{resources=this.discover(id);}catch{/* Missing visuals never prevent Soul editing. */}}
    return {saved,applied:this.applied(id),selected:id===this.selectedId(),memory:{directory:join(this.directory(id),'memory'),initialized:existsSync(join(this.directory(id),'memory/memory.sqlite3'))||existsSync(join(this.directory(id),'memory.sqlite3'))},resources};}
  list():ProfileSummary[]{return this.ids().map(id=>{const detail=this.get(id);let invalid=false;try{this.validate(detail.saved.adaptor,detail.saved);}catch{invalid=true;}return {id,displayName:detail.saved.profile.displayName,selected:detail.selected,revision:detail.saved.revision,appliedRevision:detail.applied?.revision??null,modelName:detail.saved.adaptor.model?basename(detail.saved.adaptor.model.manifest):null,visualStatus:!detail.saved.modelVersion?'unconfigured':invalid?'invalid':detail.applied?.revision===detail.saved.revision?'ready':'pending'};});}
  create(name:string,language='zh'):ProfileDetail{if(typeof name!=='string'||!name.trim()||name.length>128)throw new Error('invalid_name');const seed=createDefaultProfile();const id='profile_'+randomUUID().replaceAll('-','');const profile={...seed.profile,id,displayName:name.trim(),active:false};this.write(this.canonical({profile,soulText:defaultSoulMarkdown(language,profile.displayName),revision:1,adaptor:emptyAvatarAdaptor(),modelVersion:''}));return this.get(id);}
  save(id:string,input:ProfileSaveInput):ProfileDetail{
    const previous=this.read(id);const {soul:ignored,...profile}=profileSchema.parse(input.profile);
    profile.displayName=profile.displayName.trim();if(!profile.displayName||profile.displayName.length>128)throw new Error('invalid_name');if(profile.id!==id)throw new Error('invalid_profile_id');
    if(input.soulText!==undefined&&(typeof input.soulText!=='string'||input.expectedSoulFingerprint!==previous.soulFingerprint))throw new Error('soul_conflict');
    const soulText=input.soulText??previous.soulText;if(Buffer.byteLength(soulText,'utf8')>SOUL_MAX_BYTES)throw new Error('soul_too_large');
    profile.createdAt=previous.profile.createdAt;profile.updatedAt=previous.profile.updatedAt;profile.active=previous.profile.active;
    const parsedVoice=profileVoiceSchema.safeParse(input.voice??previous.voice);if(!parsedVoice.success)throw new Error('invalid_voice');
    const voice=parsedVoice.data;
    for(const path of voiceSecretPaths)if(!voiceSecret(voice,path))voiceSecret(voice,path,input.clearSecrets?.includes(path)?'':voiceSecret(previous.voice,path));
    if(voice.chat_model.base_url&&!/^https?:\/\//.test(voice.chat_model.base_url))throw new Error('invalid_voice');
    const adaptor=JSON.stringify(input.adaptor)===JSON.stringify(previous.adaptor)?structuredClone(previous.adaptor):this.validate(input.adaptor,previous);adaptor.revision=previous.revision;
    if(JSON.stringify({profile,adaptor,soulText,voice})===JSON.stringify({profile:previous.profile,adaptor:previous.adaptor,soulText:previous.soulText,voice:previous.voice}))return this.get(id);
    if(this.fingerprint(readFileSync(this.soulPath(id),'utf8'))!==previous.soulFingerprint)throw new Error('soul_conflict');
    const revision=previous.revision+1;profile.updatedAt=new Date().toISOString();this.write(this.canonical({...previous,profile,soulText,voice,revision,adaptor:{...adaptor,revision}}));return this.get(id);
  }
  /** One-time legacy model adoption preserves the last applied Soul and any separately saved edits. */
  adoptMigratedModel(id:string):void{const old=this.applied(id),saved=this.read(id);if(old&&!old.adaptor.model&&saved.adaptor.model)this.atomic(join(this.directory(id),'applied.json'),{...old,modelVersion:saved.modelVersion,adaptor:{...saved.adaptor,revision:old.revision}});}
  markApplied(id:string,revision:number):ProfileDetail{const saved=this.read(id);if(saved.revision!==revision)throw new Error('config_changed');validateSoul(saved.soulText);profileSchema.parse(saved.profile);this.validate(saved.adaptor,saved,false);this.atomic(join(this.directory(id),'applied.json'),saved);return this.get(id);}
  delete(id:string,guard:ProfileDeleteGuard):void{this.get(id);if(this.ids().length<=1)throw new Error('last_profile');if(id===this.selectedId())throw new Error('active_profile');if(!guard.allowed)throw new Error(guard.reason||'profile_cleanup_incomplete');rmSync(this.directory(id),{recursive:true});}
  private packageDirectory(id:string,version:string):string{if(!VERSION.test(version))throw new Error('import_failed');const profile=this.directory(id);const path=join(profile,'live2d/packages',version,'model');for(let current=path;current!==profile;current=dirname(current)){if(existsSync(current)&&lstatSync(current).isSymbolicLink())throw new Error('invalid_resource');}return path;}
  resolveResource(id:string,version:string,file:string):string{const profile=this.read(id);const applied=this.applied(id);if(profile.modelVersion!==version&&applied?.modelVersion!==version)throw new Error('import_failed');return confined(this.packageDirectory(id,version),file.startsWith('model/')?file.slice(6):file);}
  importModel(id:string,sourceFolder:string,manifestRelative:string):ProfileDetail{
    this.read(id);const source=resolve(sourceFolder);resourcePath(manifestRelative);if(!manifestRelative.endsWith('.model3.json'))throw new Error('invalid_adaptor');
    const files=walk(source);manifestReferences(source,manifestRelative);const hash=createHash('sha256');const buffer=Buffer.allocUnsafe(1024*1024);for(const file of files){hash.update(file);hash.update('\0');const fd=openSync(confined(source,file),'r');try{let count:number;while((count=readSync(fd,buffer,0,buffer.length,null))>0)hash.update(buffer.subarray(0,count));}finally{closeSync(fd);}}hash.update(manifestRelative);const version=hash.digest('hex');
    const base=this.owned(join(this.directory(id),'live2d/packages'));mkdirSync(base,{recursive:true});const temp=join(base,'.import-'+randomUUID());
    try{mkdirSync(join(temp,'model'),{recursive:true});for(const file of files){const target=this.owned(join(temp,'model',file));mkdirSync(dirname(target),{recursive:true});copyFileSync(confined(source,file),target);}manifestReferences(join(temp,'model'),manifestRelative);const destination=this.owned(join(base,version));if(existsSync(destination))rmSync(temp,{recursive:true});else renameSync(temp,destination);
      const current=this.read(id);if(current.modelVersion===version)return this.get(id);const revision=current.revision+1;const adaptor={...emptyAvatarAdaptor(),revision,model:{manifest:'model/'+manifestRelative}};this.write({...current,revision,adaptor,modelVersion:version});return this.get(id);
    }catch(error){rmSync(temp,{recursive:true,force:true});throw error;}
  }
  discover(id:string):AvatarResource[]{const config=this.read(id);if(!config.modelVersion||!config.adaptor.model)return [];const root=this.packageDirectory(id,config.modelVersion);const refs=manifestReferences(root,config.adaptor.model.manifest.slice(6));const verified=this.verified(id,config.modelVersion);return walk(root).filter(file=>refs.has(file)||file.endsWith('.motion3.json')||file.endsWith('.exp3.json')).map(file=>{const kind=refs.get(file)??(file.endsWith('.motion3.json')?'motion':'expression');let error:string|undefined;try{const data=resourceJson(confined(root,file));if(kind==='motion'?(data.Version!==3||!Array.isArray(data.Curves)||!file.endsWith('.motion3.json')):(!Array.isArray(data.Parameters)||!file.endsWith('.exp3.json')))error='invalid_resource';}catch(cause){error=cause instanceof Error?cause.message:'invalid_resource';}return {file:'model/'+file,kind,referenced:refs.has(file),verified:!error&&verified.includes('model/'+file),...(error?{error}:{})};});}
  private verified(id:string,version:string):string[]{const path=join(dirname(this.packageDirectory(id,version)),'verified.json');return existsSync(path)?this.json<string[]>(path):[];}
  verifyResources(id:string,version:string,files:string[]):ProfileDetail{const config=this.read(id);if(version!==config.modelVersion)throw new Error('resource_unverified');const available=new Set(this.discover(id).filter(r=>!r.error).map(r=>r.file));if(!Array.isArray(files)||files.length>10000||files.some(file=>!available.has(file)))throw new Error('resource_unverified');this.atomic(join(dirname(this.packageDirectory(id,version)),'verified.json'),[...new Set([...this.verified(id,version),...files])].sort());return this.get(id);}
  private validate(input:AvatarAdaptor,config:ProfileConfig,requireResources=true):AvatarAdaptor {
    const value=adaptorSchema.parse(input);if(value.model)resourcePath(value.model.manifest);if(JSON.stringify(value.model)!==JSON.stringify(config.adaptor.model))throw new Error('import_failed');const entries=Object.entries(value.entries);if(entries.length>AVATAR_LIMITS.entries||entries.filter(([,e])=>e.agent_enabled).length>AVATAR_LIMITS.agentEntries)throw new Error('entry_limit');
    const resources=new Map((requireResources&&config.modelVersion?this.discover(config.profile.id):[]).map(r=>[r.file,r]));
    for(const [name,entry]of entries){if(!NAME.test(name)||['none','neutral','constructor','prototype','__proto__'].includes(name))throw new Error('invalid_adaptor');resourcePath(entry.source.file);const resource=resources.get(entry.source.file);if(requireResources&&(!resource||resource.kind!==entry.kind||((entry.agent_enabled||Object.values(value.states).flat().includes(name))&&!resource.verified)))throw new Error('resource_unverified');if(entry.kind==='expression'?entry.playback.mode!=='hold':entry.playback.mode==='hold')throw new Error('invalid_adaptor');if(entry.agent_enabled&&entry.playback.mode==='loop')throw new Error('invalid_adaptor');}
    for(const state of stateNames){const names=value.states[state];if(new Set(names).size!==names.length)throw new Error('invalid_adaptor');for(const name of names){const e=value.entries[name];if(!e||(e.kind==='motion'?e.playback.mode!=='loop':e.playback.mode!=='hold'))throw new Error('invalid_adaptor');}}
    if(Object.values(value.states).flat().length>AVATAR_LIMITS.stateBindings)throw new Error('entry_limit');
    if(requireResources){if(config.modelVersion)value.validated_resources=this.verified(config.profile.id,config.modelVersion);else delete value.validated_resources;}
    return value;
  }
  private async migrateLegacy():Promise<void>{
    const legacy=this.options.legacyDatabasePath;let profiles:Profile[]=[];const backupRoot=this.owned(join(this.root,'.legacy-backup'));
    if(legacy&&existsSync(legacy)){mkdirSync(backupRoot,{recursive:true});const db=new Database(legacy,{readonly:true});try{const backup=this.owned(join(backupRoot,'profiles.sqlite3'));this.owned(backup+'.tmp');if(!existsSync(backup)){const temporary=backup+'.tmp';await db.backup(temporary);renameSync(temporary,backup);}const rows=db.prepare(`select p.*,s.id as soul_id,s.name as soul_name,s.persona,s.tone,s.proactive_level,s.principles_json,s.taboos_json,s.reminder_style,s.reply_preference,s.speaking_style,s.relationship,s.boundaries_json,s.topic_preferences_json,s.created_at as soul_created_at,s.updated_at as soul_updated_at from profiles p left join souls s on s.profile_id=p.id`).all();profiles=rows.map(row=>mapProfile(row as Parameters<typeof mapProfile>[0]));}finally{db.close();}}
    let persona:PersonaSettings|undefined;const settings=this.options.legacySettingsPath;if(settings&&existsSync(settings)){mkdirSync(backupRoot,{recursive:true});if(!existsSync(join(backupRoot,'settings.json')))copyFileSync(settings,this.owned(join(backupRoot,'settings.json')));persona=json<{persona?:PersonaSettings}>(settings).persona;}
    if(!profiles.length&&persona&&!this.ids().length){const seed=createDefaultProfile();profiles=[{...seed.profile,soul:seed.soul}];}
    for(const profile of profiles){checkedId(profile.id);const pending=join(this.directory(profile.id),'.legacy-import-pending.json');if(existsSync(join(this.directory(profile.id),'profile.json'))&&!existsSync(pending))continue;const original=this.canonical({profile,revision:1,adaptor:emptyAvatarAdaptor(),modelVersion:''});this.atomic(pending,{sourceId:profile.id});this.write(original);this.atomic(join(this.directory(profile.id),'applied.json'),original);
      if(profile.active){this.select(profile.id);if(persona&&profile.soul){const saved=structuredClone(profile);saved.displayName=persona.name;saved.soul={...profile.soul,name:persona.name,persona:persona.shortPersonaDescription,tone:persona.tone,proactiveLevel:persona.proactiveLevel,principles:persona.principles,taboos:persona.taboos,reminderStyle:persona.reminderStyle,replyPreference:persona.replyPreference,relationship:persona.relationshipStyle};this.save(profile.id,{profile:saved,soulText:legacySoulMarkdown(saved.soul,saved.displayName),expectedSoulFingerprint:original.soulFingerprint,adaptor:original.adaptor});}}
      rmSync(pending,{force:true});
    }
    // Backend performs its own closed-handle Memory migration before opening a Profile runtime.
  }
}

/** Convert only at the Main IPC seam; runtime snapshots keep their original credentials. */
export function publicProfileDetail(detail:ProfileDetail):ProfileDetail {
 const result=structuredClone(detail);result.secrets={};result.secretPreviews={};
 for(const path of voiceSecretPaths){
  const key=voiceSecret(result.saved.voice,path),characters=Array.from(key);
  result.secrets[path]=Boolean(key);
  result.secretPreviews[path]=characters.length<=8?key:characters.slice(0,4).join('')+'•'.repeat(characters.length-8)+characters.slice(-4).join('');
  voiceSecret(result.saved.voice,path,'');if(result.applied)voiceSecret(result.applied.voice,path,'');
 }
 return result;
}
