<!-- Displays bounded desktop telemetry as an event workspace and a single-scroll inspector.
     Stable event identities anchor paused reading while Main continues collecting; selected
     details retain one immutable event. Snapshot/reset fencing uses the existing IPC contract,
     without initializing chat history, Pet media, or a second diagnostic transport.
     Acoustic values retain six decimal places so quiet input remains visible in scalar details. -->
<template>
  <main class="live-debug-window" data-live-debug>
    <header class="debug-header">
      <div class="debug-title"><h1>{{ label('title') }}</h1><span class="capture-status" :class="{enabled:debug?.enabled}">{{ label(debug?.enabled?'enabled':'disabled') }}</span></div>
      <div class="debug-controls"><button :disabled="clearing" @click="clear">{{ label('clear') }}</button><button :title="debug?.logDirectory" @click="openFolder">{{ label('folder') }}</button></div>
    </header>
    <p v-if="error || debug?.error" role="alert" class="debug-error">{{ error || debug?.error }}</p>
    <nav class="compact-tabs" :aria-label="label('view')"><button :aria-pressed="compactView==='events'" @click="compactView='events'">{{ label('events') }}</button><button :aria-pressed="compactView==='inspector'" @click="compactView='inspector'">{{ label('inspector') }}</button></nav>
    <div class="debug-workspace" :data-view="compactView">
      <section class="event-section" :aria-label="label('events')">
        <div class="event-heading"><h2>{{ label('events') }} <small>{{ filtered.length }} / {{ debug?.events.length ?? 0 }} {{ label('matchingRetained') }}</small></h2><span>{{ label('dropped') }}: {{ debug?.dropped ?? 0 }}</span></div>
        <div class="debug-filters">
          <label>{{ label('source') }}<select v-model="source" name="debug-source"><option value="">{{ label('all') }}</option><option v-for="item in ['backend','pet','main']" :key="item" :value="item">{{ label(item) }}</option></select></label>
          <label>{{ label('stage') }}<select v-model="stageFilter" name="debug-stage"><option value="">{{ label('all') }}</option><option v-for="item in eventStages" :key="item" :value="item">{{ label(item) }}</option></select></label>
          <label>{{ label('level') }}<select v-model="level" name="debug-level"><option value="">{{ label('all') }}</option><option v-for="item in ['info','warning','error']" :key="item" :value="item">{{ label(item) }}</option></select></label>
          <input v-model="search" name="debug-search" type="search" :placeholder="label('search')" :aria-label="label('search')" />
        </div>
        <p v-if="historyExpired" data-history-expired class="reading-notice" role="status">{{ label('historyExpired') }}</p>
        <div ref="eventList" class="event-list" @scroll="onScroll" @wheel="onWheel" @keydown="onListKey">
          <p v-if="!visible.length" class="empty-events">{{ label('empty') }}</p>
          <button v-for="event in visible" :key="eventKey(event)" data-debug-event :data-event-key="eventKey(event)" :class="['event-row',event.level,{selected:selected && eventKey(selected)===eventKey(event)}]" :aria-pressed="!!selected && eventKey(selected)===eventKey(event)" @click="selectEvent(event)">
            <time>{{ time(event.timestamp) }}</time><span class="event-origin" :title="`${label(event.source)} · ${label(event.stage)}`">{{ label(event.source) }} · {{ label(event.stage) }}</span>
            <span class="event-message"><strong>{{ event.name }}</strong><span class="event-summary">{{ summary(event) }}</span></span><span class="event-level">{{ label(event.level) }}</span>
          </button>
        </div>
        <footer class="debug-pagination">
          <div class="reading-controls"><label><input :checked="autoScroll" name="debug-autoscroll" type="checkbox" @change="toggleFollow" />{{ label('autoScroll') }}</label><button v-if="newEvents" data-new-events @click="latest">{{ label('newEvents') }} {{ newEvents }}</button></div>
          <div class="page-controls"><span>{{ rangeStart }}–{{ rangeEnd }} / {{ filtered.length }}</span><button :disabled="!rangeStart || rangeStart===1" @click="movePage(-1)">{{ label('older') }}</button><button :disabled="rangeEnd>=filtered.length" @click="movePage(1)">{{ label('newer') }}</button><button @click="latest">{{ label('latest') }}</button></div>
        </footer>
      </section>
      <aside class="debug-inspector" :aria-label="label('inspector')">
        <div class="inspector-tabs" role="tablist" :aria-label="label('inspector')" @keydown="onTabKey">
          <button v-for="tab in tabs" :id="`tab-${tab}`" :key="tab" role="tab" :aria-selected="activeTab===tab" :aria-controls="`panel-${tab}`" :tabindex="activeTab===tab?0:-1" @click="activeTab=tab">{{ label(tab) }}</button>
        </div>
        <div v-show="activeTab==='state'" id="panel-state" class="inspector-content" role="tabpanel" aria-labelledby="tab-state" tabindex="0">
          <details v-for="group in groups" :key="group.name" data-state-group open class="state-group">
            <summary>{{ label(group.name) }}</summary>
            <section v-for="stage in group.stages" :key="stage" :data-stage="stage"><h3>{{ label(stage) }}</h3>
              <dl v-if="Object.keys(stateFields(stage)).length"><template v-for="(value,key) in stateFields(stage)" :key="key"><dt :title="String(key)">{{ label(String(key)) }}</dt><dd>{{ format(String(key),value) }}</dd></template></dl><p v-else class="muted">{{ label('missing') }}</p>
            </section>
          </details>
          <button class="text-link" @click="activeTab='conversation'">{{ label('conversation') }}</button>
        </div>
        <div v-show="activeTab==='conversation'" id="panel-conversation" class="inspector-content transcripts" role="tabpanel" aria-labelledby="tab-conversation" tabindex="0" data-transcripts>
          <article v-for="(value,key) in transcripts" :key="key"><h3>{{ label(key) }}</h3><p>{{ value || label('missing') }}</p></article>
        </div>
        <div v-show="activeTab==='eventDetails'" id="panel-eventDetails" class="inspector-content" role="tabpanel" aria-labelledby="tab-eventDetails" tabindex="0">
          <article v-if="selected" data-event-detail><h2>{{ selected.name }}</h2><p v-if="selectedExpired" class="reading-notice">{{ label('selectedExpired') }}</p>
            <dl><template v-for="(value,key) in selectedMetadata" :key="key"><dt>{{ label(String(key)) }}</dt><dd>{{ format(String(key),value) }}</dd></template></dl>
            <h3 class="detail-heading">{{ label('details') }}</h3><dl><template v-for="(value,key) in selected.details" :key="key"><dt :title="String(key)">{{ label(String(key)) }}</dt><dd>{{ format(String(key),value) }}</dd></template></dl>
          </article><p v-else class="muted">{{ label('selectEvent') }}</p>
        </div>
      </aside>
    </div>
  </main>
</template>
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import { appendDebugEvents, type LiveDebugEvent, type LiveDebugState, type LiveDebugUpdate } from '../../shared/contracts/liveDebug';
import { debugText } from './text';
const language=ref('zh');
const label=(key:string)=>debugText(language.value,key);
const debug=shallowRef<LiveDebugState>();
const error=ref(''),clearing=ref(false),autoScroll=ref(true);
const source=ref(''),stageFilter=ref(''),level=ref(''),search=ref('');
const eventList=ref<HTMLElement>();
const tabs=['state','conversation','eventDetails'] as const;
const activeTab=ref<typeof tabs[number]>('state'),compactView=ref('events');
const selected=shallowRef<LiveDebugEvent>();
const pausedKeys=shallowRef<string[]>([]),newEvents=ref(0),historyExpired=ref(false);
const groups=[{name:'sessionGroup',stages:['connection','transport']},{name:'inputGroup',stages:['input','capture']},{name:'recognitionGroup',stages:['stt','eou']},{name:'modelGroup',stages:['model']},{name:'outputGroup',stages:['tts','playback']},{name:'deviceGroup',stages:['devicePlayback']}];
const stages=groups.flatMap(group=>group.stages);
const eventStages=computed(()=>[...new Set([...stages,...(debug.value?.events.map(event=>event.stage) ?? [])])]);
const filtered=computed(()=>{
 const query=search.value.trim().toLocaleLowerCase();
 return (debug.value?.events ?? []).filter(event=>(!source.value || event.source===source.value)&&(!stageFilter.value || event.stage===stageFilter.value)&&(!level.value || event.level===level.value)&&(!query || JSON.stringify(event).toLocaleLowerCase().includes(query)));
});
const visible=computed(()=>{if(autoScroll.value)return filtered.value.slice(-200);const keys=new Set(pausedKeys.value);return filtered.value.filter(event=>keys.has(eventKey(event)));});
const rangeStart=computed(()=>visible.value.length?filtered.value.findIndex(event=>eventKey(event)===eventKey(visible.value[0]!))+1:0);
const rangeEnd=computed(()=>rangeStart.value?rangeStart.value+visible.value.length-1:0);
const selectedExpired=computed(()=>!!selected.value&&!debug.value?.events.some(event=>eventKey(event)===eventKey(selected.value!)));
const selectedMetadata=computed(()=>{if(!selected.value)return {};const {details,...metadata}=selected.value;return metadata;});
const textFields=new Set(['text','stablePrefix','mutableSuffix','stable_text','mutable_text','stableText','mutableText','stable','mutable','reply_text','reply']);
function stateFields(stage:string){return Object.fromEntries(Object.entries(debug.value?.state[stage]??{}).filter(([key])=>!textFields.has(key)));}
const transcripts=computed(()=>{
 const current=debug.value?.state ?? {},stt=current.stt ?? {},model=current.model ?? {};
 return {stable:stt.stablePrefix ?? stt.stable_text ?? stt.stableText ?? stt.stable ?? '',mutable:stt.mutableSuffix ?? stt.mutable_text ?? stt.mutableText ?? stt.mutable ?? '',reply:current.playback?.text ?? model.text ?? model.reply_text ?? model.reply ?? current.reply?.text ?? ''};
});
function eventKey(event:LiveDebugEvent){return `${event.captureId}:${event.source}:${event.instanceId}:${event.seq}`;}
function time(timestamp:number){const date=new Date(timestamp);return date.toLocaleTimeString(language.value==='zh'?'zh-CN':language.value==='ja'?'ja-JP':'en-GB',{hour12:false})+'.'+String(date.getMilliseconds()).padStart(3,'0');}
function format(key:string,value:unknown):string {
 if(value===null||value===undefined||value==='')return label('missing');
 if(typeof value==='boolean')return label(value?'yes':'no');
 if(typeof value==='number'){
  if(['timestamp','updatedAt','startedAt'].includes(key))return time(value);
  const precision=['rms','rmsMin','rmsMax','noise','noiseFloor','threshold','thresholdMin','thresholdMax'].includes(key)?6:3;
  const number=Number.isInteger(value)?String(value):String(Number(value.toFixed(precision)));
  if(/Ms$|_ms$/.test(key))return `${number} ms`;
  if(key==='sampleRate')return `${number} Hz`;
  if(/bytes$/i.test(key)||key==='bufferedAmount')return `${number} B`;
  if(['contextTime','endContextTime','outputTime','outputContextTime'].includes(key))return `${number} s`;
  return number;
 }
 if(['source','stage','level'].includes(key))return label(String(value));
 return String(value);
}
function summary(event:LiveDebugEvent){
 const key=['reason','text','stablePrefix','mutableSuffix','state','status','elapsedMs','durationMs','latencyMs'].find(key=>event.details[key]!==undefined&&event.details[key]!==null&&event.details[key]!=='');
 return key?format(key,event.details[key]):'';
}
let previousScroll=0,scrollRevision=0,resetReading=false;
function pause(){if(!autoScroll.value)return;scrollRevision++;pausedKeys.value=visible.value.map(eventKey);autoScroll.value=false;newEvents.value=0;}
async function scrollTo(bottom:boolean){const revision=++scrollRevision;await nextTick();if(revision!==scrollRevision || (bottom&&!autoScroll.value))return;if(eventList.value){eventList.value.scrollTop=bottom?eventList.value.scrollHeight:0;previousScroll=eventList.value.scrollTop;}}
function onScroll(){if(!eventList.value?.clientHeight)return;const top=eventList.value?.scrollTop??0;if(top<previousScroll-1)pause();previousScroll=top;}
function onWheel(event:WheelEvent){if(event.deltaY<0)pause();}
function onListKey(event:KeyboardEvent){if(['ArrowUp','PageUp','Home'].includes(event.key))pause();}
function latest(){autoScroll.value=true;pausedKeys.value=[];newEvents.value=0;historyExpired.value=false;void scrollTo(true);}
function toggleFollow(event:Event){if((event.target as HTMLInputElement).checked)latest();else pause();}
function movePage(direction:number){
 const start=rangeStart.value-1,end=rangeEnd.value;pause();
 pausedKeys.value=(direction<0?filtered.value.slice(Math.max(0,start-200),start):filtered.value.slice(end,end+200)).map(eventKey);
 historyExpired.value=false;void scrollTo(false);
}
function selectEvent(event:LiveDebugEvent){pause();selected.value={...event,details:{...event.details}};activeTab.value='eventDetails';compactView.value='inspector';}
function onTabKey(event:KeyboardEvent){
 if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
 event.preventDefault();const index=tabs.indexOf(activeTab.value);
 activeTab.value=tabs[event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length]!;
 void nextTick(()=>document.getElementById(`tab-${activeTab.value}`)?.focus());
}
function mergeEvents(current:LiveDebugEvent[],incoming:LiveDebugEvent[]){
 const unique=new Map<string,LiveDebugEvent>();for(const event of [...current,...incoming])unique.set(eventKey(event),event);
 return appendDebugEvents([],Array.from(unique.values()));
}
let lastResetRevision=-1,disposed=false;let unsubscribe:(()=>void)|undefined;
function accept(update:LiveDebugUpdate){
 if(disposed || update.revision <= (debug.value?.revision ?? -1))return;
 if(update.reset){resetReading=true;lastResetRevision=update.revision;selected.value=undefined;pausedKeys.value=[];newEvents.value=0;historyExpired.value=false;}
 debug.value={...update,events:mergeEvents(update.reset?[]:debug.value?.events ?? [],update.events)};
 if(update.reset&&!autoScroll.value)pausedKeys.value=filtered.value.slice(-200).map(eventKey);
}
function snapshot(value:LiveDebugState){
 if(disposed)return;
 if(!debug.value || value.revision>=debug.value.revision){debug.value={...value,events:mergeEvents([],value.events)};return;}
 // Preserve newer subscription deltas but never restore history invalidated by a reset.
 if(lastResetRevision<=value.revision)debug.value={...debug.value,events:mergeEvents(value.events,debug.value.events)};
}
async function clear(){clearing.value=true;error.value='';try{const value=await window.aurabot.clearLiveDebugLogs();accept({...value,reset:true});}catch(e){error.value=String(e);}finally{clearing.value=false;}}
async function openFolder(){try{await window.aurabot.openLiveDebugLogDirectory();}catch(e){error.value=String(e);}}
// Post-render observation is essential: synchronous watchers can scroll the previous DOM.
watch(filtered,(events,previous)=>{
 if(resetReading){resetReading=false;newEvents.value=0;historyExpired.value=false;if(!autoScroll.value)pausedKeys.value=events.slice(-200).map(eventKey);void scrollTo(autoScroll.value);return;}
 const oldKeys=new Set(previous.map(eventKey)),keys=new Set(events.map(eventKey));
 const incoming=events.filter(event=>!oldKeys.has(eventKey(event))).length;
 if(autoScroll.value){if(incoming)void scrollTo(true);return;}
 newEvents.value+=incoming;
 if(pausedKeys.value.some(key=>!keys.has(key))){historyExpired.value=true;pausedKeys.value=events.slice(0,200).map(eventKey);void scrollTo(false);}
},{flush:'post'});
watch(compactView,view=>{if(view==='events'&&autoScroll.value)void scrollTo(true);});
watch([source,stageFilter,level,search],()=>{resetReading=true;pausedKeys.value=filtered.value.slice(-200).map(eventKey);newEvents.value=0;historyExpired.value=false;void scrollTo(autoScroll.value);},{flush:'sync'});
onMounted(()=>{
 unsubscribe=window.aurabot.onLiveDebugChanged(accept);
 void window.aurabot.getLiveDebugState().then(snapshot).catch(e=>{if(!disposed)error.value=String(e);});
 void window.aurabot.getSettings().then(value=>{if(!disposed)language.value=value.config.desktop.pet.displayLanguage;}).catch(e=>{if(!disposed)error.value=String(e);});
});
onBeforeUnmount(()=>{disposed=true;unsubscribe?.();});
</script>
<style scoped>
.live-debug-window{box-sizing:border-box;height:100%;padding:16px;color:var(--aura-text);background:var(--aura-bg);display:flex;flex-direction:column;gap:12px;overflow:hidden;font-size:13px}
button,select,input[type=search]{min-height:30px;padding:5px 8px;border:1px solid var(--aura-border);border-radius:6px;background:var(--aura-surface);color:var(--aura-text);font:inherit}button{cursor:pointer}button:hover:not(:disabled){background:var(--aura-surface-soft)}button:disabled{opacity:.45;cursor:default}button:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible,[tabindex]:focus-visible{outline:2px solid var(--aura-accent);outline-offset:-2px}
.debug-header,.debug-title,.debug-controls{display:flex;align-items:center;gap:12px}.debug-header{justify-content:space-between;flex-shrink:0;-webkit-app-region:drag}.debug-controls,button,input,select{-webkit-app-region:no-drag}.debug-title{flex-wrap:wrap;gap:6px 12px}h1{font-size:20px;margin:0}h2{font-size:14px;margin:0}h3{font-size:13px;margin:0 0 8px}p{margin:0}.capture-status,.muted{color:var(--aura-muted)}.capture-status.enabled{color:var(--aura-accent)}
.debug-workspace{display:grid;grid-template-columns:minmax(0,1fr) minmax(340px,33%);gap:16px;flex:1;min-height:0;min-width:0}.event-section,.debug-inspector{display:flex;flex-direction:column;min-height:0;min-width:0;gap:10px}.event-heading{display:flex;align-items:baseline;justify-content:space-between;gap:8px}.event-heading small,.event-heading>span{font-size:11px;color:var(--aura-muted);font-weight:400}.debug-filters{display:grid;grid-template-columns:100px 140px 85px minmax(80px,1fr);gap:8px}.debug-filters label{display:flex;flex-direction:column;gap:4px}.debug-filters input{align-self:end;min-width:0}.debug-filters select{width:100%;min-width:0}
.event-list{overflow:auto;min-height:0;flex:1;border:1px solid var(--aura-border);border-radius:8px;overscroll-behavior:contain;overflow-anchor:none}.event-row{width:100%;height:34px;min-height:34px;border:0;border-bottom:1px solid var(--aura-border);border-radius:0;padding:4px 8px;display:grid;grid-template-columns:94px 100px minmax(0,1fr) 40px;gap:8px;align-items:center;text-align:left;background:transparent}.event-row.selected{background:var(--aura-surface-soft);box-shadow:inset 3px 0 var(--aura-accent)}.event-row time{font-size:11px;white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--aura-muted)}.event-origin{font-size:11px;color:var(--aura-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.event-message{min-width:0;display:flex;align-items:baseline;gap:10px;overflow:hidden}.event-message strong{font-size:12px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex-shrink:0;max-width:70%}.event-summary{font-size:11px;color:var(--aura-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.event-level{font-size:11px;text-align:right;color:var(--aura-muted)}.event-row.error .event-level,.debug-error{color:var(--aura-danger)}.event-row.warning .event-level{color:#b7791f}
.debug-pagination{display:flex;flex-direction:column;gap:8px;flex-shrink:0;font-size:12px}.reading-controls,.page-controls{display:flex;align-items:center;gap:8px;min-height:30px}.reading-controls label{display:flex;align-items:center;gap:6px}.page-controls{justify-content:flex-end}.page-controls>span{margin-right:auto;font-variant-numeric:tabular-nums;color:var(--aura-muted)}.reading-notice{font-size:12px;color:var(--aura-muted);line-height:1.5}.empty-events{padding:16px;color:var(--aura-muted)}.debug-error{overflow-wrap:anywhere;max-height:48px;overflow:auto}
.debug-inspector{border:1px solid var(--aura-border);border-radius:8px;gap:0;background:var(--aura-surface)}.inspector-tabs{display:flex;padding:6px;gap:4px;border-bottom:1px solid var(--aura-border);flex-shrink:0}.inspector-tabs button{flex:1;border:0;background:transparent;font-size:12px;white-space:nowrap;padding:6px}.inspector-tabs button[aria-selected=true],.compact-tabs button[aria-pressed=true]{background:var(--aura-surface-soft);color:var(--aura-accent);font-weight:600}.inspector-content{overflow:auto;overscroll-behavior:contain;min-height:0;flex:1;padding:12px;overflow-wrap:anywhere}.state-group{border-bottom:1px solid var(--aura-border);padding:0 0 12px;margin-bottom:12px}.state-group>summary{font-weight:600;cursor:pointer;line-height:28px}.state-group section{margin-top:12px}.state-group h3{color:var(--aura-muted);font-weight:500}dl{display:grid;grid-template-columns:minmax(100px,40%) minmax(0,1fr);gap:8px 12px;margin:0;font-size:12px;line-height:1.6}dt{color:var(--aura-muted);overflow-wrap:break-word}dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.transcripts article+article{margin-top:24px}.transcripts p{line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere}.detail-heading{margin:20px 0 10px}[data-event-detail]>h2{margin-bottom:16px;overflow-wrap:anywhere}[data-event-detail]>.reading-notice{margin-bottom:12px}.text-link{margin-top:4px}.compact-tabs{display:none}
@media(max-width:1050px){.debug-filters{grid-template-columns:85px 115px 75px minmax(80px,1fr)}.event-row{grid-template-columns:86px 75px minmax(0,1fr) 35px;gap:6px}.event-origin{font-size:10px}}
@media(max-width:899px){.live-debug-window{padding:12px;gap:10px}.compact-tabs{display:flex;gap:8px}.debug-workspace{display:block}.debug-workspace>.event-section,.debug-workspace>.debug-inspector{height:100%;box-sizing:border-box}.debug-workspace[data-view=events]>.debug-inspector,.debug-workspace[data-view=inspector]>.event-section{display:none}.debug-filters{grid-template-columns:100px 140px 85px minmax(80px,1fr)}.event-row{grid-template-columns:94px 120px minmax(0,1fr) 40px}.debug-pagination{flex-direction:row;justify-content:space-between;flex-wrap:wrap}.debug-header{gap:8px}.debug-title h1{font-size:17px}}
@media(max-width:650px){.debug-header{flex-wrap:wrap}.debug-filters{grid-template-columns:repeat(3,minmax(0,1fr))}.debug-filters input{grid-column:1/-1}.event-row{grid-template-columns:86px 70px minmax(0,1fr) 34px;gap:4px}}
:global(.app-shell-live-debug){height:100%;overflow:hidden}
</style>
