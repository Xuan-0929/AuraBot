/** Stores only accepted user messages and delivered say_to_user clauses in an independent SQLite file.
 * Electron Main validates delivery sources before calling record; stable per-profile IDs deduplicate
 * receipts. Cursor pagination avoids loading all history, and nothing here is fed back into Context.
 */
import Database from 'better-sqlite3';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import type { HistoryEntry, HistoryPage } from '../shared/contracts/desktop';
import type { Message } from '../shared/schemas/message';
const entrySchema=z.object({id:z.string().min(1).max(256),role:z.enum(['user','assistant']),content:z.string().min(1).max(128*1024)});
interface Row { sequence:number; profile_id:string; id:string; role:'user'|'assistant'; content:string; created_at:string; }
function message(row:Row):Message { return {id:row.id,profileId:row.profile_id,sessionId:`main:${row.profile_id}`,role:row.role,content:row.content,createdAt:row.created_at,attachments:[]}; }
export class ConversationHistory {
  private db:Database.Database;
  constructor(root:string) {
    mkdirSync(root,{recursive:true}); this.db=new Database(join(root,'conversation-history.sqlite3'));
    this.db.pragma('journal_mode = WAL');
    this.db.exec('CREATE TABLE IF NOT EXISTS messages(sequence INTEGER PRIMARY KEY AUTOINCREMENT,profile_id TEXT NOT NULL,id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(profile_id,id)); CREATE INDEX IF NOT EXISTS messages_profile_sequence ON messages(profile_id,sequence)');
  }
  record(profile:string,input:HistoryEntry):Message {
    const entry=entrySchema.parse(input);
    const old=this.db.prepare('SELECT * FROM messages WHERE profile_id=? AND id=?').get(profile,entry.id) as Row|undefined;
    if(old && (old.role!==entry.role || (entry.role==='assistant'&&old.content!==entry.content))) throw new Error('交付记录标识冲突');
    this.db.prepare('INSERT INTO messages(profile_id,id,role,content,created_at) VALUES(?,?,?,?,?) ON CONFLICT(profile_id,id) DO UPDATE SET content=excluded.content').run(profile,entry.id,entry.role,entry.content,new Date().toISOString());
    return message(this.db.prepare('SELECT * FROM messages WHERE profile_id=? AND id=?').get(profile,entry.id) as Row);
  }
  list(profile:string,before?:number,limit=50):HistoryPage {
    if(!Number.isInteger(limit)||limit<1||limit>100||before!==undefined&&(!Number.isSafeInteger(before)||before<1)) throw new Error('无效历史分页');
    const rows=this.db.prepare('SELECT * FROM messages WHERE profile_id=? AND sequence<? ORDER BY sequence DESC LIMIT ?').all(profile,before??Number.MAX_SAFE_INTEGER,limit+1) as Row[];
    const more=rows.length>limit; const page=rows.slice(0,limit).reverse();
    return {messages:page.map(message),nextCursor:more?page[0]?.sequence:undefined};
  }
  close() { this.db.close(); }
}
