/** Exercises Profile voice persistence through the real repository and renderer-safe projection.
 * A/B isolation, immutable applied snapshots and write-only secret replacement/clearing protect
 * conversation ownership across mode switches, model imports and process reloads. */
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {afterEach,expect,it} from 'vitest';
import {ProfileRepository,publicProfileDetail} from '../../../src/core/profiles/profileRepository';
const roots:string[]=[];
afterEach(()=>roots.splice(0).forEach(root=>rmSync(root,{recursive:true,force:true})));
it('isolates A/B credentials and preserves applied settings until explicitly applied',async()=>{
 const root=mkdtempSync(join(tmpdir(),'aura-profile-voice-'));roots.push(root);
 const repo=new ProfileRepository({profilesRoot:root});await repo.initialize();
 const a=repo.get(repo.selectedId()).saved,b=repo.create('B').saved;
 const voice=structuredClone(a.voice);voice.chat_model.model_name='A-chat';voice.chat_model.api_key='A-private-chat-key';voice.speech.api_key='A-private-pipeline-key';voice.speech.realtime.api_key='A-private-realtime-key';voice.speech.realtime.voice='A-voice';
 const save=(id:string,detail:ReturnType<typeof repo.get>,extra={})=>repo.save(id,{profile:detail.saved.profile,adaptor:detail.saved.adaptor,voice:detail.saved.voice,...extra});
 const saved=save(a.profile.id,repo.get(a.profile.id),{voice});repo.markApplied(a.profile.id,saved.saved.revision);
 const publicA=publicProfileDetail(repo.get(a.profile.id));
 expect(publicA.saved.voice.chat_model.api_key).toBe('');expect(publicA.applied!.voice.speech.api_key).toBe('');expect(publicA.secrets?.['speech.realtime.api_key']).toBe(true);
 expect(JSON.stringify(publicA)).not.toContain('A-private');expect(repo.get(b.profile.id).saved.voice.chat_model.api_key).toBe('');
 publicA.saved.voice.speech.mode='realtime';const switched=save(a.profile.id,publicA);
 expect(switched.saved.voice.speech.mode).toBe('realtime');expect(switched.applied!.voice.speech.mode).toBe('pipeline');expect(switched.saved.voice.speech.api_key).toBe('A-private-pipeline-key');
 const cleared=save(a.profile.id,publicProfileDetail(switched),{clearSecrets:['speech.realtime.api_key']});
 expect(cleared.saved.voice.speech.realtime.api_key).toBe('');expect(cleared.saved.voice.chat_model.api_key).toBe('A-private-chat-key');
 expect(repo.create('C').saved.voice.speech.api_key).toBe('');
 const restored=new ProfileRepository({profilesRoot:root});await restored.initialize();
 expect(restored.get(a.profile.id).saved.voice).toEqual(cleared.saved.voice);expect(restored.get(b.profile.id).saved.voice).toEqual(b.voice);
 expect(()=>save(a.profile.id,publicProfileDetail(cleared),{voice:{...voice,speech:{...voice.speech,mode:'both'}}})).toThrow('invalid_voice');
});
