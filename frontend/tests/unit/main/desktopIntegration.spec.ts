/** Checks saved/applied configuration separation, secret redaction and durable delivered history.
 * History display cutoffs persist per Profile while pagination retains all original messages. */
import { mkdtempSync, readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import { DesktopConfigStore } from '../../../src/main/desktopConfigStore';
import { ConversationHistory } from '../../../src/main/conversationHistory';
const roots: string[] = [];
function directory() { const root = mkdtempSync(join(tmpdir(), 'aura-desktop-')); roots.push(root); return root; }
afterEach(() => roots.splice(0).forEach(root => rmSync(root, { recursive: true, force: true })));
it('saves without changing the applied configuration and never returns stored keys', () => {
  const root = directory();
  const store = new DesktopConfigStore(root);
  store.markApplied(store.snapshot());
  const before = store.status();
  const draft = store.publicConfig();
  draft.backend.chat_model.api_key = 'private-test-key';
  draft.backend.chat_model.model_name = 'new-model';
  store.save(draft);
  expect(store.status().appliedRevision).toBe(before.appliedRevision);
  expect(store.status().pending).toBe(true);
  expect(JSON.stringify(store.publicConfig())).not.toContain('private-test-key');
  expect(store.snapshot().backend.chat_model.api_key).toBe('private-test-key');
  store.save(store.publicConfig());
  expect(store.snapshot().backend.chat_model.api_key).toBe('private-test-key');
  expect(statSync(store.path).mode & 0o777).toBe(0o600);
  expect(JSON.parse(readFileSync(store.path, 'utf8')).version).toBe(2);
});
it('deduplicates delivered records, scopes profiles and paginates after reopen', () => {
  const root = directory();
  let history = new ConversationHistory(root);
  for (let n=0;n<4;n++) history.record('p', { id: `clause-${n}`, role:'assistant', content:`said ${n}` });
  history.record('p', { id:'clause-3', role:'assistant', content:'said 3' });
  history.record('q', { id:'clause-3', role:'assistant', content:'other profile' });
  const page = history.list('p', undefined, 2);
  expect(page.messages.map(m=>m.content)).toEqual(['said 2','said 3']);
  expect(page.nextCursor).toBeDefined();
  expect(history.list('p', page.nextCursor,2).messages.map(m=>m.content)).toEqual(['said 0','said 1']);
  history.close(); history = new ConversationHistory(root);
  expect(history.list('p').messages).toHaveLength(4);
  history.close();
});

it('persists per-profile display clearing without removing history or hiding new messages', () => {
  const root=directory();let history=new ConversationHistory(root);
  for(let n=0;n<65;n++)history.record('p',{id:`old-${n}`,role:'user',content:`old ${n}`});
  history.record('q',{id:'other',role:'assistant',content:'other profile'});
  history.clearDisplay('p');
  expect(history.visible('p').messages).toEqual([]);
  const cursor=history.visible('p').nextCursor;
  expect(cursor).toBeDefined();
  const older=history.list('p',cursor);
  expect(older.messages).toHaveLength(50);
  expect(history.list('p',older.nextCursor).messages).toHaveLength(15);
  expect(history.visible('q').messages).toHaveLength(1);
  history.close();history=new ConversationHistory(root);
  expect(history.visible('p').messages).toEqual([]);
  history.record('p',{id:'new',role:'assistant',content:'new reply'});
  const visible=history.visible('p');
  expect(visible.messages.map(m=>m.id)).toEqual(['new']);
  expect(history.list('p',visible.nextCursor).messages.map(m=>m.id)).not.toContain('new');
  history.clearDisplay('empty');expect(history.visible('empty')).toEqual({messages:[],nextCursor:undefined});
  history.record('empty',{id:'first',role:'user',content:'first'});
  expect(history.visible('empty').messages).toHaveLength(1);
  history.close();
});
