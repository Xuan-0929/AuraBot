<!-- Displays desktop-owned Live telemetry without initializing chat history or Pet media.
     Revisioned snapshots/deltas merge through the shared bounded event cache; clear/reset
     fences stale deliveries. Native details, filters and 200-row pages keep the DOM bounded,
     while auto-scroll changes viewing only and never controls telemetry capture. -->
<template>
  <main class="live-debug-window" data-live-debug>
    <header class="debug-header">
      <div><h1>{{ label('title') }}</h1><span class="capture-status" :class="{enabled:debug?.enabled}">{{ label(debug?.enabled?'enabled':'disabled') }}</span></div>
      <div class="debug-controls">
        <button type="button" :disabled="clearing" @click="clear">{{ label('clear') }}</button>
        <button type="button" @click="openFolder">{{ label('folder') }}</button>
        <label><input v-model="autoScroll" name="debug-autoscroll" type="checkbox" />{{ label('autoScroll') }}</label>
      </div>
    </header>
    <p v-if="error || debug?.error" role="alert" class="debug-error">{{ error || debug?.error }}</p>
    <section class="state-section" :aria-label="label('state')">
      <h2>{{ label('state') }}</h2>
      <div class="state-cards">
        <article v-for="stage in stages" :key="stage" class="state-card" :data-stage="stage">
          <h3>{{ label(stage) }}</h3>
          <dl v-if="Object.keys(debug?.state[stage] ?? {}).length"><template v-for="(value,key) in debug?.state[stage]" :key="key"><dt :title="String(key)">{{ label(String(key)) }}</dt><dd>{{ value ?? '—' }}</dd></template></dl>
          <p v-else>{{ label('waiting') }}</p>
        </article>
      </div>
      <div class="transcripts"><article v-for="(value,key) in transcripts" :key="key"><h3>{{ label(key) }}</h3><p>{{ value || '—' }}</p></article></div>
    </section>
    <section class="event-section" :aria-label="label('events')">
      <div class="event-heading"><h2>{{ label('events') }} <small>{{ filtered.length }} / {{ debug?.events.length ?? 0 }}</small></h2><span>{{ label('dropped') }}: {{ debug?.dropped ?? 0 }}</span></div>
      <div class="debug-filters">
        <label>{{ label('source') }}<select v-model="source" name="debug-source"><option value="">{{ label('all') }}</option><option v-for="item in ['backend','pet','main']" :key="item" :value="item">{{ label(item) }}</option></select></label>
        <label>{{ label('stage') }}<select v-model="stageFilter" name="debug-stage"><option value="">{{ label('all') }}</option><option v-for="item in eventStages" :key="item" :value="item">{{ label(item) }}</option></select></label>
        <label>{{ label('level') }}<select v-model="level" name="debug-level"><option value="">{{ label('all') }}</option><option v-for="item in ['info','warning','error']" :key="item" :value="item">{{ label(item) }}</option></select></label>
        <input v-model="search" name="debug-search" type="search" :placeholder="label('search')" :aria-label="label('search')" />
      </div>
      <div ref="eventList" class="event-list">
        <p v-if="!visible.length" class="empty-events">{{ label('empty') }}</p>
        <article v-for="event in visible" :key="eventKey(event)" data-debug-event :class="['event-row',event.level]">
          <details><summary><time>{{ time(event.timestamp) }}</time><span>{{ label(event.source) }} · {{ label(event.stage) }}</span><strong>{{ event.name }}</strong><span class="event-level">{{ label(event.level) }}</span></summary>
            <dl><template v-for="(value,key) in event" :key="key"><template v-if="key!=='details'"><dt>{{ label(String(key)) }}</dt><dd>{{ value ?? '—' }}</dd></template></template></dl>
            <h4>{{ label('details') }}</h4><dl><template v-for="(value,key) in event.details" :key="key"><dt :title="String(key)">{{ label(String(key)) }}</dt><dd>{{ value ?? '—' }}</dd></template></dl>
          </details>
        </article>
      </div>
      <footer class="debug-pagination"><button :disabled="page===0" @click="page=0">{{ label('latest') }}</button><button :disabled="page===0" @click="page--">{{ label('newer') }}</button><span>{{ page+1 }} / {{ pageCount }}</span><button :disabled="page+1>=pageCount" @click="page++">{{ label('older') }}</button></footer>
    </section>
    <p v-if="debug?.logDirectory" class="log-directory" :title="debug.logDirectory">{{ label('logDirectory') }}: {{ debug.logDirectory }}</p>
  </main>
</template>
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import { appendDebugEvents, type LiveDebugEvent, type LiveDebugState, type LiveDebugUpdate } from '../../shared/contracts/liveDebug';
import { debugText } from './text';
const language=ref('zh');
const label=(key:string)=>debugText(language.value,key);
const debug=shallowRef<LiveDebugState>();
const error=ref('');const clearing=ref(false);const autoScroll=ref(true);
const source=ref('');const stageFilter=ref('');const level=ref('');const search=ref('');const page=ref(0);
const eventList=ref<HTMLElement>();
const stages=['connection','input','stt','eou','model','tts','playback','transport','capture','devicePlayback'];
const eventStages=computed(()=>[...new Set([...stages,...(debug.value?.events.map(event=>event.stage) ?? [])])]);
const filtered=computed(()=>{
 const query=search.value.trim().toLocaleLowerCase();
 return (debug.value?.events ?? []).filter(event=>(!source.value || event.source===source.value)&&(!stageFilter.value || event.stage===stageFilter.value)&&(!level.value || event.level===level.value)&&(!query || JSON.stringify(event).toLocaleLowerCase().includes(query)));
});
const pageCount=computed(()=>Math.max(1,Math.ceil(filtered.value.length/200)));
const visible=computed(()=>{const end=Math.max(0,filtered.value.length-page.value*200);return filtered.value.slice(Math.max(0,end-200),end);});
const transcripts=computed(()=>{
 const current=debug.value?.state ?? {};const stt=current.stt ?? {};const model=current.model ?? {};
 return {stable:stt.stablePrefix ?? stt.stable_text ?? stt.stableText ?? stt.stable ?? '',mutable:stt.mutableSuffix ?? stt.mutable_text ?? stt.mutableText ?? stt.mutable ?? '',reply:current.playback?.text ?? model.text ?? model.reply_text ?? model.reply ?? current.reply?.text ?? ''};
});
function mergeEvents(current:LiveDebugEvent[],incoming:LiveDebugEvent[]){
 const unique=new Map<string,LiveDebugEvent>();
 for(const event of [...current,...incoming])unique.set(eventKey(event),event);
 return appendDebugEvents([],Array.from(unique.values()));
}
function eventKey(event:LiveDebugEvent){return `${event.captureId}:${event.source}:${event.instanceId}:${event.seq}`;}
function time(timestamp:number){return new Date(timestamp).toLocaleTimeString(language.value==='zh'?'zh-CN':language.value==='ja'?'ja-JP':'en-US',{hour12:false});}
let lastResetRevision=-1;let disposed=false;let unsubscribe:(()=>void)|undefined;
function accept(update:LiveDebugUpdate){
 if(disposed || update.revision <= (debug.value?.revision ?? -1))return;
 if(update.reset){lastResetRevision=update.revision;page.value=0;}
 debug.value={...update,events:mergeEvents(update.reset?[]:debug.value?.events ?? [],update.events)};
}
function snapshot(value:LiveDebugState){
 if(disposed)return;
 if(!debug.value || value.revision>=debug.value.revision){debug.value={...value,events:mergeEvents([],value.events)};return;}
 // A subscription can deliver newer deltas while getState is in flight. Preserve those
 // deltas, and include older snapshot history only when no subsequent reset invalidated it.
 if(lastResetRevision<=value.revision)debug.value={...debug.value,events:mergeEvents(value.events,debug.value.events)};
}
async function clear(){clearing.value=true;error.value='';try{const value=await window.aurabot.clearLiveDebugLogs();accept({...value,reset:true});}catch(e){error.value=String(e);}finally{clearing.value=false;}}
async function openFolder(){try{await window.aurabot.openLiveDebugLogDirectory();}catch(e){error.value=String(e);}}
watch([source,stageFilter,level,search],()=>{page.value=0;});
watch(pageCount,count=>{page.value=Math.min(page.value,count-1);});
watch([()=>debug.value?.revision,autoScroll,page],async()=>{if(autoScroll.value&&page.value===0){await nextTick();if(eventList.value)eventList.value.scrollTop=eventList.value.scrollHeight;}});
onMounted(()=>{
 unsubscribe=window.aurabot.onLiveDebugChanged(accept);
 void window.aurabot.getLiveDebugState().then(snapshot).catch(e=>{if(!disposed)error.value=String(e);});
 void window.aurabot.getSettings().then(value=>{if(!disposed)language.value=value.config.desktop.pet.displayLanguage;}).catch(e=>{if(!disposed)error.value=String(e);});
});
onBeforeUnmount(()=>{disposed=true;unsubscribe?.();});
</script>
<style scoped>
.live-debug-window{box-sizing:border-box;height:100%;padding:12px;color:var(--aura-text);background:var(--aura-bg);display:flex;flex-direction:column;gap:8px;overflow:auto;font-size:12px}
button,select,input[type=search]{min-height:30px;padding:5px 8px;border:1px solid var(--aura-border);border-radius:6px;background:var(--aura-surface);color:var(--aura-text)}button:hover:not(:disabled){background:var(--aura-surface-soft)}button:disabled{opacity:.45}button{cursor:pointer}select{min-width:0}
.debug-header{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;-webkit-app-region:drag}.debug-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;-webkit-app-region:no-drag}.debug-controls label{display:flex;gap:6px;align-items:center}h1{font-size:19px;margin:0 0 6px}h2{font-size:14px;margin:0 0 8px}h3{font-size:12px;margin:0 0 6px}h4{margin:8px 0}button,input,select{-webkit-app-region:no-drag}.capture-status{color:var(--aura-muted)}.capture-status.enabled{color:var(--aura-accent)}
.state-section{flex:none}.state-cards{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:6px}.state-card{padding:8px;border:1px solid var(--aura-border);border-radius:6px;background:var(--aura-surface-soft);min-width:0;max-height:58px;box-sizing:border-box;overflow:auto}.state-card p{margin:0;color:var(--aura-muted)}dl{display:grid;grid-template-columns:minmax(70px,auto) minmax(0,1fr);gap:4px 8px;margin:0}dt{color:var(--aura-muted);overflow-wrap:anywhere}dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.state-card dl{grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:4px}.state-card dd{margin:0}.state-card h3{position:sticky;top:-8px;background:var(--aura-surface-soft)}.transcripts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:8px}.transcripts article{min-width:0;padding:6px;border:1px solid var(--aura-border);border-radius:6px}.transcripts p{white-space:pre-wrap;overflow-wrap:anywhere;max-height:32px;overflow:auto;margin:0}
.event-section{display:flex;flex-direction:column;min-height:150px;flex:1}.event-heading{display:flex;align-items:baseline;justify-content:space-between}.event-heading small,.event-heading>span{font-size:11px;color:var(--aura-muted)}.debug-filters{display:flex;align-items:end;gap:8px;flex-wrap:wrap;margin-bottom:8px}.debug-filters label{display:flex;flex-direction:column;gap:4px;min-width:85px}.debug-filters input{flex:1;min-width:110px}.debug-filters select{max-width:150px}.event-list{overflow:auto;min-height:80px;flex:1;border:1px solid var(--aura-border);border-radius:6px}.event-row{border-bottom:1px solid var(--aura-border);padding:7px 8px}.event-row summary{display:flex;gap:8px;align-items:baseline;cursor:pointer;list-style:none}.event-row summary:before{content:'▸'}.event-row details[open]>summary:before{content:'▾'}.event-row time{white-space:nowrap;color:var(--aura-muted);font-variant-numeric:tabular-nums}.event-row strong{overflow-wrap:anywhere;flex:1;font-weight:500}.event-level{font-size:10px;color:var(--aura-muted)}.event-row.error .event-level,.debug-error{color:var(--aura-danger)}.event-row.warning .event-level{color:#b7791f}.event-row details[open]>dl{margin-top:10px}.debug-pagination{display:flex;align-items:center;justify-content:flex-end;gap:8px;padding-top:8px}.empty-events{padding:8px;color:var(--aura-muted)}.log-directory{margin:0;color:var(--aura-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:11px}.debug-error{margin:0;overflow-wrap:anywhere}
@media(max-width:600px){.state-cards{grid-template-columns:repeat(2,minmax(0,1fr))}.transcripts{grid-template-columns:1fr}.live-debug-window{padding:12px}.event-row summary{flex-wrap:wrap}}
:global(.app-shell-live-debug){height:100%;overflow:hidden}
</style>
