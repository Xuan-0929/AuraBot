/** Verifies the desktop Profile storage boundary persists identity and persona across reopen.
 * The same saved Soul supplies the realtime prompt, and a fresh database contains only Profile tables.
 */
import { afterEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import { ProfileStorage } from '../../../../src/core/profiles/profileStorage';
import { buildSoulPrompt } from '../../../../src/core/profiles/soulPrompt';
const open: {root:string; storage:ProfileStorage}[]=[];
function createStorage(prefix:string) {
 const root=mkdtempSync(join(tmpdir(),prefix));const storage=new ProfileStorage(join(root,'profile.sqlite3'));open.push({root,storage});
 return storage;
}
function cleanupStorage(){for(const {root,storage} of open.splice(0)){storage.close();rmSync(root,{recursive:true,force:true});}}

afterEach(cleanupStorage);

describe("ProfileStorage", () => {
  it("maps the active profile and soul into editable persona settings", () => {
    const storage = createStorage("aurabot-persona-settings-");
    const settings = storage.getActivePersonaSettings();

    expect(settings).toMatchObject({
      name: "Aura",
      shortPersonaDescription: expect.any(String),
      tone: expect.any(String),
      relationshipStyle: expect.any(String),
      proactiveLevel: "balanced",
      principles: expect.any(Array),
      taboos: expect.any(Array),
      reminderStyle: "",
      replyPreference: ""
    });
  });

  it("persists light and semi-pro persona fields back into profile and soul storage", () => {
    const storage = createStorage("aurabot-persona-settings-update-");

    const updated = storage.updateActivePersonaSettings({
      name: "Auri",
      shortPersonaDescription: "会陪我聊天，也会更主动提醒我。",
      tone: "直接、放松、短句",
      relationshipStyle: "像熟悉的长期搭子",
      proactiveLevel: "high",
      principles: ["先接住用户情绪，再推进事情。"],
      taboos: ["不要像客服一样说话。"],
      reminderStyle: "像顺手提醒一句，不要太正式。",
      replyPreference: "多用短句，少用解释腔。"
    });

    expect(updated).toMatchObject({
      displayName: "Auri",
      soul: {
        name: "Auri",
        persona: "会陪我聊天，也会更主动提醒我。",
        tone: "直接、放松、短句",
        relationship: "像熟悉的长期搭子",
        proactiveLevel: "high",
        principles: ["先接住用户情绪，再推进事情。"],
        taboos: ["不要像客服一样说话。"],
        reminderStyle: "像顺手提醒一句，不要太正式。",
        replyPreference: "多用短句，少用解释腔。"
      }
    });

    const reloaded = storage.getOrSeedActiveProfile();
    expect(reloaded).toMatchObject(updated);
  });
});

it('reopens current identity/persona and creates only profiles/souls tables',()=>{
 const storage=createStorage('aura-profile-schema-');const before=storage.getOrSeedActiveProfile();
 storage.updateActivePersonaSettings({...storage.getActivePersonaSettings(),name:'Auri',tone:'Quiet and direct'});const path=join(open[0].root,'profile.sqlite3');storage.close();
 const reopened=new ProfileStorage(path);open[0].storage=reopened;const profile=reopened.getOrSeedActiveProfile();expect(profile.id).toBe(before.id);expect(profile.createdAt).toBe(before.createdAt);expect(profile.displayName).toBe('Auri');expect(buildSoulPrompt(profile.soul)).toContain('Quiet and direct');
 const db=new Database(path,{readonly:true});try{expect(db.prepare("select name from sqlite_master where type='table' order by name").all()).toEqual([{name:'profiles'},{name:'souls'}]);}finally{db.close();}
});
