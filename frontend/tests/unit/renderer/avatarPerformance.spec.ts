/** Exercises SAY-level cue ownership, completion and pause independently of network arrival.
 * Production director callbacks stand in for rendering only; all identity/timer decisions run here.
 */
import {it,expect} from 'vitest';
import {PerformanceDirector,type PerformanceEvent} from '../../../src/features/live2d/performance';
const cue={profile_id:'a',generation:2,config_revision:3,say_id:'s',invoke_id:'i',call_id:'c',avatar:{motion:'nod',expression:'smile'}};
it('starts once on actual playback, keeps expression across clauses, and fences revisions',()=>{
 const starts:any[]=[];let finishes=0;
 const p=new PerformanceDirector(cue,{start:a=>starts.push(a),finish:()=>finishes++,level:()=>{}});
 const send=(type:PerformanceEvent['type'],extra:any={})=>p.accept({type,cue,clauseId:'c1',mode:'voice',...extra});
 send('offer');expect(starts).toHaveLength(0);send('start');send('start');send('complete');expect(finishes).toBe(0);
 send('offer',{clauseId:'c2'});send('start',{clauseId:'c2'});send('closed',{clauseCount:2});expect(finishes).toBe(0);send('complete',{clauseId:'c2'});expect(finishes).toBe(1);expect(starts).toHaveLength(1);
 send('start',{cue:{...cue,say_id:'stale',config_revision:1}});expect(starts).toHaveLength(1);
 send('start',{cue:{...cue,say_id:'next'}});expect(starts).toHaveLength(2);
});
it('text holds visibly without delaying ACK and cancel cannot resurrect a cue',()=>{
 let starts=0,finishes=0;const p=new PerformanceDirector(cue,{start:()=>starts++,finish:()=>finishes++,level:()=>{}});
 p.accept({type:'start',cue,mode:'text',text:'你好'});p.accept({type:'closed',cue,clauseCount:1});p.accept({type:'complete',cue,clauseId:'c'});
 expect(finishes).toBe(0);p.advance(1499);expect(finishes).toBe(0);p.advance(1);expect(finishes).toBe(1);
 p.accept({type:'start',cue});expect(starts).toBe(1);
 const next={...cue,say_id:'next'};p.accept({type:'offer',cue:next});p.accept({type:'cancel',cue:next});p.accept({type:'start',cue:next});expect(starts).toBe(1);
});

it('audio pause never blocks text after reset and stale envelopes cannot alter current output',()=>{
 let starts=0,level=0;const p=new PerformanceDirector(cue,{start:()=>starts++,finish:()=>{},level:v=>level=v});
 p.accept({type:'pause'});p.accept({type:'start',cue,mode:'text'});expect(starts).toBe(1);
 p.accept({type:'reset'});const next={...cue,say_id:'next'};p.accept({type:'start',cue:next,mode:'voice'});expect(starts).toBe(2);
 p.accept({type:'level',cue:next,level:.8});expect(level).toBe(.8);
 p.accept({type:'level',cue:{...next,generation:1},level:.2});expect(level).toBe(.8);
});
