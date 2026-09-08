/** Owns Profile configuration files and model packages inside the configured shared profiles root.
 * A replayable journal commits Soul/adaptor/identity together; immutable applied snapshots isolate
 * running conversations from edits. Legacy SQLite is backed up consistently before import. Context,
 * Memory and Qdrant remain Backend-owned: this repository only reports their paths and existence.
 */
import Database from 'better-sqlite3';
import { closeSync, copyFileSync, existsSync, lstatSync, mkdirSync, openSync, readFileSync, readSync, readdirSync, realpathSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { z } from 'zod';
import { profileSchema, type Profile } from '../../shared/schemas/profile';
import { AVATAR_LIMITS, emptyAvatarAdaptor, type AvatarAdaptor, type AvatarResource } from '../../shared/contracts/avatar';
import type { ProfileConfig, ProfileDeleteGuard, ProfileDetail, ProfileSaveInput, ProfileSummary } from '../../shared/contracts/profiles';
import type { PersonaSettings } from '../../shared/contracts/personaSettings';
import { createDefaultProfile } from './soulService';
import { mapProfile } from './profileStorage';

const ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const VERSION = /^[a-f0-9]{64}$/;
const NAME = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const stateNames = ['idle', 'listening', 'thinking', 'speaking'] as const;
const finiteFade = z.number().finite().min(0).max(AVATAR_LIMITS.fadeMs).optional();
const adaptorSchema = z.object({
  schema_version: z.literal(1), revision: z.number().int().positive(),
  model: z.object({manifest:z.string()}).strict().nullable(),
  validated_resources:z.array(z.string()).optional(),
  entries:z.record(z.string(),z.object({kind:z.enum(['motion','expression']),source:z.object({file:z.string()}).strict(),description:z.string().max(AVATAR_LIMITS.descriptionLength),agent_enabled:z.boolean(),playback:z.object({mode:z.enum(['once','loop','hold']),fade_in_ms:finiteFade,fade_out_ms:finiteFade}).strict()}).strict()),
  states:z.object({idle:z.string().nullable(),listening:z.string().nullable(),thinking:z.string().nullable(),speaking:z.string().nullable()}).strict()
}).strict();
function json<T>(path:string):T{return JSON.parse(readFileSync(path,'utf8')) as T;}
function resourceJson(path:string):any{if(lstatSync(path).size>4*1024*1024)throw new Error('模型 JSON 超过 4 MiB');return json(path);}
function atomic(path:string,value:unknown):void{mkdirSync(dirname(path),{recursive:true});const tmp=path+'.tmp';writeFileSync(tmp,JSON.stringify(value,null,2)+'\n',{mode:0o600});renameSync(tmp,path);}
function checkedId(id:string):string{if(!ID.test(id))throw new Error('Profile ID 无效');return id;}
/** Resolves the same relative path chain as Python MainAgentSettings.from_config. */
export function resolveProfilesRoot(configPath:string,dataDir:string,profileDir:string):string{return resolve(resolve(dirname(resolve(configPath)),dataDir),profileDir);}
function resourcePath(file:string):string {
  if(!file || /[\\\x00-\x1f?#]/.test(file) || file.includes('%') || isAbsolute(file) || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(file))throw new Error('模型资源路径无效');
  if(file.split('/').some(part=>!part || part==='.' || part==='..'))throw new Error('模型资源路径越界');
  return file;
}
function confined(root:string,file:string):string {
  resourcePath(file);const path=resolve(root,file);const base=realpathSync(root);
  if(!existsSync(path))throw new Error(`模型资源缺失：${file}`);
  const actual=realpathSync(path);if(actual!==base&&!actual.startsWith(base+sep))throw new Error('模型资源越界');
  for(let current=path;current!==root;current=dirname(current)){if(lstatSync(current).isSymbolicLink())throw new Error('模型资源不允许符号链接');}
  if(!lstatSync(actual).isFile())throw new Error('模型资源不是文件');return actual;
}
function walk(root:string):string[]{
  if(lstatSync(root).isSymbolicLink())throw new Error('模型目录不允许符号链接');
  const files:string[]=[];let bytes=0;
  const visit=(dir:string)=>{for(const name of readdirSync(dir).sort()){const path=join(dir,name);const stat=lstatSync(path);if(stat.isSymbolicLink())throw new Error('模型包不允许符号链接');if(stat.isDirectory())visit(path);else if(stat.isFile()){bytes+=stat.size;if(bytes>1024*1024*1024 || files.length>=10000)throw new Error('模型包超过 1 GiB 或 10000 文件上限');files.push(relative(root,path).split(sep).join('/'));}else throw new Error('模型包包含不支持的文件');}};
  visit(root);return files;
}
export function findModelManifests(folder:string):string[]{return walk(resolve(folder)).filter(file=>file.endsWith('.model3.json'));}
function manifestReferences(root:string,manifest:string):Map<string,'motion'|'expression'> {
  const value=resourceJson(confined(root,manifest));if(value.Version!==3 || !value.FileReferences || typeof value.FileReferences.Moc!=='string')throw new Error('不支持的 model3 模型清单');
  const refs=value.FileReferences;const base=dirname(manifest);const resources=new Map<string,'motion'|'expression'>();
  const add=(file:unknown,kind?:'motion'|'expression')=>{if(typeof file!=='string')throw new Error('模型清单资源引用无效');resourcePath(file);const relativeFile=base==='.'?file:`${base}/${file}`;confined(root,relativeFile);if(kind)resources.set(relativeFile,kind);};
  add(refs.Moc);if(!Array.isArray(refs.Textures))throw new Error('模型清单缺少纹理列表');refs.Textures.forEach((file:unknown)=>add(file));
  for(const key of ['Physics','Pose','DisplayInfo','UserData'])if(refs[key])add(refs[key]);
  if(refs.Motions)for(const motions of Object.values(refs.Motions)){if(!Array.isArray(motions))throw new Error('动作列表无效');motions.forEach(m=>add(m.File,'motion'));}
  if(refs.Expressions){if(!Array.isArray(refs.Expressions))throw new Error('表情列表无效');refs.Expressions.forEach((e:any)=>add(e.File,'expression'));}
  return resources;
}
export interface ProfileRepositoryOptions {profilesRoot:string;legacyDatabasePath?:string;legacySettingsPath?:string}
export class ProfileRepository {
  readonly root:string;
  /** Check every existing repository-owned path segment, including dangling links and .tmp files. */
  private owned(path:string):string {
    const absolute=resolve(path),part=relative(this.root,absolute);
    if(part==='..'||part.startsWith('..'+sep)||isAbsolute(part))throw new Error('Profile 路径越界');
    let current=this.root;
    for(const segment of ['',...part.split(sep).filter(Boolean)]){
      if(segment)current=join(current,segment);
      try{if(lstatSync(current).isSymbolicLink())throw new Error('Profile 文件和目录不允许符号链接');}
      catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}
    }
    return absolute;
  }
  private json<T>(path:string):T{return json<T>(this.owned(path));}
  private atomic(path:string,value:unknown):void{this.owned(path);this.owned(path+'.tmp');atomic(path,value);}
  private checkConfigWrites(id:string):void{
    for(const file of ['profile.json','soul.json','live2d/adaptor.json','.save-journal.json']){
      const path=join(this.directory(id),file);this.owned(path);this.owned(path+'.tmp');
    }
  }
  constructor(private readonly options:ProfileRepositoryOptions){this.root=resolve(options.profilesRoot);}
  private directory(id:string):string{return this.owned(join(this.root,checkedId(id)));}
  async initialize():Promise<void>{
    this.owned(this.root);mkdirSync(this.root,{recursive:true});
    if(!existsSync(join(this.root,'.migration-v1.json'))){await this.migrateLegacy();this.atomic(join(this.root,'.migration-v1.json'),{version:1,completedAt:new Date().toISOString()});}
    if(!this.ids().length){const seed=createDefaultProfile();this.write({profile:{...seed.profile,soul:seed.soul},revision:1,adaptor:emptyAvatarAdaptor(),modelVersion:''});}
    if(!this.selectedId())this.select(this.ids()[0]);
  }
  private ids():string[]{return readdirSync(this.root).filter(id=>ID.test(id)&&existsSync(join(this.directory(id),'profile.json')));}
  selectedId():string{const path=join(this.root,'index.json');if(!existsSync(path))return '';const id=this.json<{selectedId:string}>(path).selectedId;return ID.test(id)&&existsSync(join(this.directory(id),'profile.json'))?id:'';}
  select(id:string):void{this.get(id);this.atomic(join(this.root,'index.json'),{selectedId:id});}
  private recover(id:string):void{const journal=join(this.directory(id),'.save-journal.json');if(existsSync(journal)){const config=this.json<ProfileConfig>(journal);if(config.profile.id!==id)throw new Error('Profile 保存事务身份不匹配');this.commit(config);}}
  private commit(config:ProfileConfig):void {
    this.checkConfigWrites(config.profile.id);const directory=this.directory(config.profile.id);const {soul,...profile}=config.profile;
    this.atomic(join(directory,'soul.json'),soul??null);this.atomic(join(directory,'live2d/adaptor.json'),config.adaptor);
    this.atomic(join(directory,'profile.json'),{...profile,revision:config.revision,modelVersion:config.modelVersion});
    rmSync(join(directory,'.save-journal.json'),{force:true});
  }
  private write(config:ProfileConfig):void{this.checkConfigWrites(config.profile.id);this.atomic(join(this.directory(config.profile.id),'.save-journal.json'),config);this.commit(config);}
  private read(id:string):ProfileConfig {
    this.recover(id);const directory=this.directory(id);const {revision,modelVersion,...identity}=this.json<Profile & {revision:number;modelVersion:string}>(join(directory,'profile.json'));
    const soul=this.json<Profile['soul']>(join(directory,'soul.json'));return {profile:{...identity,soul:soul??undefined},revision,modelVersion,adaptor:this.json(join(directory,'live2d/adaptor.json'))};
  }
  applied(id:string):ProfileConfig|null{const path=join(this.directory(id),'applied.json');return existsSync(path)?this.json(path):null;}
  get(id:string):ProfileDetail{const saved=this.read(id);let resources:AvatarResource[]=[];if(saved.modelVersion){try{resources=this.discover(id);}catch{/* Missing visuals never prevent Soul editing. */}}
    return {saved,applied:this.applied(id),selected:id===this.selectedId(),memory:{directory:join(this.directory(id),'memory'),initialized:existsSync(join(this.directory(id),'memory/memory.sqlite3'))||existsSync(join(this.directory(id),'memory.sqlite3'))},resources};}
  list():ProfileSummary[]{return this.ids().map(id=>{const detail=this.get(id);let invalid=false;try{this.validate(detail.saved.adaptor,detail.saved);}catch{invalid=true;}return {id,displayName:detail.saved.profile.displayName,selected:detail.selected,revision:detail.saved.revision,appliedRevision:detail.applied?.revision??null,modelName:detail.saved.adaptor.model?basename(detail.saved.adaptor.model.manifest):null,visualStatus:!detail.saved.modelVersion?'unconfigured':invalid?'invalid':detail.applied?.revision===detail.saved.revision?'ready':'pending'};});}
  create(name:string):ProfileDetail{if(!name.trim()||name.length>128)throw new Error('名称须为 1–128 个字符');const seed=createDefaultProfile();const id='profile_'+randomUUID().replaceAll('-','');const profile={...seed.profile,id,displayName:name.trim(),active:false,soul:{...seed.soul,id:'soul_'+randomUUID().replaceAll('-',''),profileId:id,name:name.trim()}};this.write({profile,revision:1,adaptor:emptyAvatarAdaptor(),modelVersion:''});return this.get(id);}
  save(id:string,input:ProfileSaveInput):ProfileDetail{
    const previous=this.read(id);const profile=profileSchema.parse(input.profile);if(profile.displayName.length>128)throw new Error('名称最多 128 个字符');if(profile.id!==id||profile.soul?.profileId!==id||profile.soul?.id!==previous.profile.soul?.id)throw new Error('不能改变 Profile 或 Soul 身份');
    profile.createdAt=previous.profile.createdAt;profile.updatedAt=previous.profile.updatedAt;profile.active=previous.profile.active;if(profile.soul&&previous.profile.soul){profile.soul.createdAt=previous.profile.soul.createdAt;profile.soul.updatedAt=previous.profile.soul.updatedAt;}
    // Unchanged visual configuration must not prevent editing Soul when optional assets disappear.
    const adaptor=JSON.stringify(input.adaptor)===JSON.stringify(previous.adaptor)?structuredClone(previous.adaptor):this.validate(input.adaptor,previous);adaptor.revision=previous.revision;
    if(JSON.stringify({profile,adaptor})===JSON.stringify({profile:previous.profile,adaptor:previous.adaptor}))return this.get(id);
    const revision=previous.revision+1;const now=new Date().toISOString();profile.updatedAt=now;if(profile.soul)profile.soul.updatedAt=now;this.write({...previous,profile,revision,adaptor:{...adaptor,revision}});return this.get(id);
  }
  /** One-time legacy model adoption preserves the last applied Soul and any separately saved edits. */
  adoptMigratedModel(id:string):void{const old=this.applied(id),saved=this.read(id);if(old&&!old.adaptor.model&&saved.adaptor.model)this.atomic(join(this.directory(id),'applied.json'),{...old,modelVersion:saved.modelVersion,adaptor:{...saved.adaptor,revision:old.revision}});}
  markApplied(id:string,revision:number):ProfileDetail{const saved=this.read(id);if(saved.revision!==revision)throw new Error('配置版本已变化，请重新应用');profileSchema.parse(saved.profile);this.validate(saved.adaptor,saved,false);this.atomic(join(this.directory(id),'applied.json'),saved);return this.get(id);}
  delete(id:string,guard:ProfileDeleteGuard):void{this.get(id);if(this.ids().length<=1)throw new Error('不能删除最后一个 Profile');if(id===this.selectedId())throw new Error('请先切换到其他 Profile');if(!guard.allowed)throw new Error(guard.reason||'Profile 仍有活动资源');rmSync(this.directory(id),{recursive:true});}
  private packageDirectory(id:string,version:string):string{if(!VERSION.test(version))throw new Error('模型版本无效');const profile=this.directory(id);const path=join(profile,'live2d/packages',version,'model');for(let current=path;current!==profile;current=dirname(current)){if(existsSync(current)&&lstatSync(current).isSymbolicLink())throw new Error('模型目录不允许符号链接');}return path;}
  resolveResource(id:string,version:string,file:string):string{const profile=this.read(id);const applied=this.applied(id);if(profile.modelVersion!==version&&applied?.modelVersion!==version)throw new Error('模型版本不属于当前保存或应用配置');return confined(this.packageDirectory(id,version),file.startsWith('model/')?file.slice(6):file);}
  importModel(id:string,sourceFolder:string,manifestRelative:string):ProfileDetail{
    const previous=this.read(id);const source=resolve(sourceFolder);resourcePath(manifestRelative);if(!manifestRelative.endsWith('.model3.json'))throw new Error('请选择 .model3.json 入口');
    const files=walk(source);manifestReferences(source,manifestRelative);const hash=createHash('sha256');const buffer=Buffer.allocUnsafe(1024*1024);for(const file of files){hash.update(file);hash.update('\0');const fd=openSync(confined(source,file),'r');try{let count:number;while((count=readSync(fd,buffer,0,buffer.length,null))>0)hash.update(buffer.subarray(0,count));}finally{closeSync(fd);}}hash.update(manifestRelative);const version=hash.digest('hex');
    const base=this.owned(join(this.directory(id),'live2d/packages'));mkdirSync(base,{recursive:true});const temp=join(base,'.import-'+randomUUID());
    try{mkdirSync(join(temp,'model'),{recursive:true});for(const file of files){const target=this.owned(join(temp,'model',file));mkdirSync(dirname(target),{recursive:true});copyFileSync(confined(source,file),target);}manifestReferences(join(temp,'model'),manifestRelative);const destination=this.owned(join(base,version));if(existsSync(destination))rmSync(temp,{recursive:true});else renameSync(temp,destination);
      if(previous.modelVersion===version)return this.get(id);const revision=previous.revision+1;const adaptor={...emptyAvatarAdaptor(),revision,model:{manifest:'model/'+manifestRelative}};this.write({...previous,revision,adaptor,modelVersion:version});return this.get(id);
    }catch(error){rmSync(temp,{recursive:true,force:true});throw error;}
  }
  discover(id:string):AvatarResource[]{const config=this.read(id);if(!config.modelVersion||!config.adaptor.model)return [];const root=this.packageDirectory(id,config.modelVersion);const refs=manifestReferences(root,config.adaptor.model.manifest.slice(6));const verified=this.verified(id,config.modelVersion);return walk(root).filter(file=>refs.has(file)||file.endsWith('.motion3.json')||file.endsWith('.exp3.json')).map(file=>{const kind=refs.get(file)??(file.endsWith('.motion3.json')?'motion':'expression');let error:string|undefined;try{const data=resourceJson(confined(root,file));if(kind==='motion'?(data.Version!==3||!Array.isArray(data.Curves)||!file.endsWith('.motion3.json')):(!Array.isArray(data.Parameters)||!file.endsWith('.exp3.json')))error='不支持的资源格式';}catch(cause){error=cause instanceof Error&&cause.message.includes('4 MiB')?cause.message:'资源 JSON 损坏或无法读取';}return {file:'model/'+file,kind,referenced:refs.has(file),verified:!error&&verified.includes('model/'+file),...(error?{error}:{})};});}
  private verified(id:string,version:string):string[]{const path=join(dirname(this.packageDirectory(id,version)),'verified.json');return existsSync(path)?this.json<string[]>(path):[];}
  verifyResources(id:string,version:string,files:string[]):ProfileDetail{const config=this.read(id);if(version!==config.modelVersion)throw new Error('过期的模型验证结果');const available=new Set(this.discover(id).filter(r=>!r.error).map(r=>r.file));if(!Array.isArray(files)||files.length>10000||files.some(file=>!available.has(file)))throw new Error('资源验证结果无效');this.atomic(join(dirname(this.packageDirectory(id,version)),'verified.json'),[...new Set([...this.verified(id,version),...files])].sort());return this.get(id);}
  private validate(input:AvatarAdaptor,config:ProfileConfig,requireResources=true):AvatarAdaptor {
    const value=adaptorSchema.parse(input);if(value.model)resourcePath(value.model.manifest);if(JSON.stringify(value.model)!==JSON.stringify(config.adaptor.model))throw new Error('模型入口只能通过导入修改');const entries=Object.entries(value.entries);if(entries.length>AVATAR_LIMITS.entries||entries.filter(([,e])=>e.agent_enabled).length>AVATAR_LIMITS.agentEntries)throw new Error('超过 128 个条目或 32 个 Agent 能力上限');
    const resources=new Map((requireResources&&config.modelVersion?this.discover(config.profile.id):[]).map(r=>[r.file,r]));
    for(const [name,entry]of entries){if(!NAME.test(name)||['none','neutral','constructor','prototype','__proto__'].includes(name))throw new Error('能力名称无效或为保留名称');resourcePath(entry.source.file);const resource=resources.get(entry.source.file);if(requireResources&&(!resource||resource.kind!==entry.kind||!resource.verified))throw new Error(`资源尚未通过播放器验证：${entry.source.file}`);if(entry.kind==='expression'?entry.playback.mode!=='hold':entry.playback.mode==='hold')throw new Error('资源类型与播放模式不兼容');if(entry.agent_enabled&&entry.playback.mode==='loop')throw new Error('Agent 不可调用循环动作');}
    for(const state of stateNames){const name=value.states[state];if(name!==null&&(!value.entries[name]||value.entries[name].kind!=='motion'||value.entries[name].playback.mode!=='loop'))throw new Error('运行状态必须绑定循环动作');}
    if(requireResources){if(config.modelVersion)value.validated_resources=this.verified(config.profile.id,config.modelVersion);else delete value.validated_resources;}
    return value;
  }
  private async migrateLegacy():Promise<void>{
    const legacy=this.options.legacyDatabasePath;let profiles:Profile[]=[];const backupRoot=this.owned(join(this.root,'.legacy-backup'));
    if(legacy&&existsSync(legacy)){mkdirSync(backupRoot,{recursive:true});const db=new Database(legacy,{readonly:true});try{const backup=this.owned(join(backupRoot,'profiles.sqlite3'));this.owned(backup+'.tmp');if(!existsSync(backup)){const temporary=backup+'.tmp';await db.backup(temporary);renameSync(temporary,backup);}const rows=db.prepare(`select p.*,s.id as soul_id,s.name as soul_name,s.persona,s.tone,s.proactive_level,s.principles_json,s.taboos_json,s.reminder_style,s.reply_preference,s.speaking_style,s.relationship,s.boundaries_json,s.topic_preferences_json,s.created_at as soul_created_at,s.updated_at as soul_updated_at from profiles p left join souls s on s.profile_id=p.id`).all();profiles=rows.map(row=>mapProfile(row as Parameters<typeof mapProfile>[0]));}finally{db.close();}}
    let persona:PersonaSettings|undefined;const settings=this.options.legacySettingsPath;if(settings&&existsSync(settings)){mkdirSync(backupRoot,{recursive:true});if(!existsSync(join(backupRoot,'settings.json')))copyFileSync(settings,this.owned(join(backupRoot,'settings.json')));persona=json<{persona?:PersonaSettings}>(settings).persona;}
    if(!profiles.length&&persona&&!this.ids().length){const seed=createDefaultProfile();profiles=[{...seed.profile,soul:seed.soul}];}
    for(const profile of profiles){checkedId(profile.id);const pending=join(this.directory(profile.id),'.legacy-import-pending.json');if(existsSync(join(this.directory(profile.id),'profile.json'))&&!existsSync(pending))continue;const original:ProfileConfig={profile,revision:1,adaptor:emptyAvatarAdaptor(),modelVersion:''};this.atomic(pending,{sourceId:profile.id});this.write(original);this.atomic(join(this.directory(profile.id),'applied.json'),original);
      if(profile.active){this.select(profile.id);if(persona&&profile.soul){const saved=structuredClone(profile);saved.displayName=persona.name;saved.soul={...profile.soul,name:persona.name,persona:persona.shortPersonaDescription,tone:persona.tone,proactiveLevel:persona.proactiveLevel,principles:persona.principles,taboos:persona.taboos,reminderStyle:persona.reminderStyle,replyPreference:persona.replyPreference,relationship:persona.relationshipStyle};this.save(profile.id,{profile:saved,adaptor:original.adaptor});}}
      rmSync(pending,{force:true});
    }
    // Backend performs its own closed-handle Memory migration before opening a Profile runtime.
  }
}
