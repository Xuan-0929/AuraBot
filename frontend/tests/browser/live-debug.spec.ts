/** Measures debug event density, full transcripts and scroll anchoring in real Chromium layout.
 * Synthetic typed subscription updates isolate CSS/scroll behavior from Main/Python lifecycle, which
 * remains covered by the Electron debug suite. Each language reloads an isolated Renderer fixture. */
import type { LiveDebugEvent } from '../../src/shared/contracts/liveDebug';
import { test, expect, capture } from './fixtures';

test('debug keeps readable geometry and anchored history across sizes and languages',async({page})=>{
  let revision=100000;
  const makeEvent=(id:number):LiveDebugEvent=>({id,source:'backend',seq:id,instanceId:'layout',captureId:'layout',timestamp:1700000000000+id,stage:'stt',name:`event-${id}`,level:'info',details:{reason:`reason-${id}`,text:`long transcript ${id} `+'完整文字 '.repeat(100)}});
  const state={connection:{profileId:'profile-with-a-very-long-identifier-'.repeat(4),connected:true,generation:12,configRevision:17},transport:{state:'active',updatedAt:1700000000000},input:{rms:0.005,threshold:0.01,vad:0.75},capture:{sampleRate:16000,echoCancellation:true,noiseSuppression:true},stt:{stablePrefix:'稳定文字 '.repeat(300),mutableSuffix:'可变文字 '.repeat(120)},eou:{probability:0.8,eouThreshold:0.9},model:{state:'running',invokeId:'invoke-'.repeat(50)},tts:{queuedClauses:3},playback:{text:'回复文字 '.repeat(300)},devicePlayback:{queuedMs:1250,started:true}};
  const feed=async(events:LiveDebugEvent[],reset=false)=>{
   await page.evaluate(update=>window.dispatchEvent(new CustomEvent('test:debug-update',{detail:update})),
    {revision:++revision,events,state,reset,enabled:true,captureId:'layout',dropped:0,logDirectory:'logs/layout',updatedAt:Date.now()});
  };
  const resize=(width:number,height:number)=>page.setViewportSize({width,height});
  const fullyVisible=()=>page.locator('.event-list').evaluate(list=>{const bounds=list.getBoundingClientRect();return [...list.querySelectorAll('[data-debug-event]')].filter(row=>{const r=row.getBoundingClientRect();return r.top>=bounds.top&&r.bottom<=bounds.bottom;}).length;});
  const scrollTop=()=>page.locator('.event-list').evaluate(list=>list.scrollTop);
  const rows=()=>page.locator('[data-debug-event]').evaluateAll(items=>items.map(item=>item.getAttribute('data-event-key')));
  for(const language of ['zh','en','ja'] as const){
   await page.goto('/?language='+language+'#live-debug');await expect(page.locator('[role=tab]').nth(1)).toHaveText(language==='zh'?'对话文字':language==='ja'?'会話テキスト':'Conversation text');
   await resize(1120,760);await feed(Array.from({length:450},(_,i)=>makeEvent(i+1)),true);
   await expect(page.locator('[data-debug-event]')).toHaveCount(200);
   if(language==='zh')await expect.poll(async()=>page.locator('.event-list').evaluate(list=>Math.abs(list.scrollHeight-list.clientHeight-list.scrollTop))).toBeLessThan(2);
   await expect.poll(fullyVisible).toBeGreaterThanOrEqual(14);
   await expect(page.locator('.debug-pagination')).toBeInViewport();
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
   expect(await page.locator('[data-state-group]').evaluateAll(groups=>groups.every(group=>getComputedStyle(group).overflowY==='visible'))).toBe(true);
   await capture(page,`live-debug-redesign-${language}`);
   await resize(800,600);await expect.poll(fullyVisible).toBeGreaterThanOrEqual(8);
   await expect(page.locator('.compact-tabs')).toBeVisible();
   await page.locator('[data-debug-event]').last().click();
   await expect(page.locator('[data-event-detail]')).toBeVisible();
   await page.locator('[role=tab]').nth(1).click();await expect(page.locator('[data-transcripts]')).toBeVisible();
   expect(await page.locator('.transcripts p').evaluateAll(items=>items.every(item=>getComputedStyle(item).overflowY==='visible'&&item.scrollHeight<=item.clientHeight+1))).toBe(true);
   await capture(page,`live-debug-redesign-${language}-text`);
   await page.locator('.compact-tabs button').first().click();
   await capture(page,`live-debug-redesign-${language}-small`);
   await resize(1360,900);await expect.poll(fullyVisible).toBeGreaterThanOrEqual(14);
   await capture(page,`live-debug-redesign-${language}-large`);
  }
  await resize(1120,760);await feed(Array.from({length:450},(_,i)=>makeEvent(i+1)),true);
  await page.locator('.page-controls button').first().click();
  await page.locator('.event-list').evaluate(list=>{list.scrollTop=200;list.dispatchEvent(new Event('scroll'));});
  const before=await rows(),top=await scrollTop();
  await feed([makeEvent(451),makeEvent(452)]);
  await expect(page.locator('[data-new-events]')).toContainText('2');expect(await rows()).toEqual(before);expect(await scrollTop()).toBe(top);
  await feed([]);expect(await scrollTop()).toBe(top);
  await page.locator('[data-new-events]').click();await expect(page.locator('[data-debug-event]').last()).toContainText('event-452');
  await expect.poll(async()=>page.locator('.event-list').evaluate(list=>Math.abs(list.scrollHeight-list.clientHeight-list.scrollTop))).toBeLessThan(2);
  await page.locator('.event-list').evaluate(list=>{list.scrollTop-=100;list.dispatchEvent(new Event('scroll'));});
  await expect(page.locator('[name=debug-autoscroll]')).not.toBeChecked();
  const paused=await scrollTop();await feed([makeEvent(453)]);await expect(page.locator('[data-new-events]')).toContainText('1');expect(await scrollTop()).toBe(paused);
  await feed(Array.from({length:1001},(_,i)=>makeEvent(i+454)));
  await expect(page.locator('[data-history-expired]')).toBeVisible();await expect(page.locator('[data-debug-event]')).toHaveCount(200);
});
