/** Defines the opt-in diagnostic boundary shared by Pet, Main and the debug window.
 * Startup mode and fixture identity accompany approved diagnostic fields. Only approved scalar facts cross IPC; byte/count caps apply before caching, displaying or logging.
 * Source sequence preserves local order; Main assigns id for its merged, bounded timeline.
 * Acoustic window counters, onset gate identity and STT teardown facts pass the same scalar whitelist into disk and UI. */
export type DebugScalar = string | number | boolean | null;
export type DebugState = Record<string, Record<string, DebugScalar>>;
export interface LiveDebugEvent {
  id?:number; source:'backend'|'pet'|'main'; seq:number; instanceId:string; captureId:string;
  timestamp:number; stage:string; name:string; level:'info'|'warning'|'error';
  profileId?:string; generation?:number; configRevision?:number;
  segmentId?:string; inputId?:string; invokeId?:string; sayId?:string; clauseId?:string;
  details:Record<string,DebugScalar>; truncated?:boolean;
}
export interface LiveDebugCapture { enabled:boolean; captureId:string; }
export interface LiveDebugState extends LiveDebugCapture {
  revision:number; events:LiveDebugEvent[]; state:DebugState; dropped:number;
  error?:string; logDirectory:string; updatedAt:number;
}
export interface LiveDebugUpdate extends LiveDebugState { reset:boolean; }
export interface BackendDebugBatch extends LiveDebugCapture {
  instanceId:string; cursor:number; events:LiveDebugEvent[]; dropped:number; state:DebugState;
}
export const DEBUG_MAX_EVENTS=1000, DEBUG_MAX_BYTES=2*1024*1024, DEBUG_EVENT_BYTES=32*1024;
export const DEBUG_STAGES=['connection','input','stt','eou','model','tts','playback','system','transport','capture','devicePlayback'];
const fields=new Set(`debug lockMemory memoryFixture state enabled voiceEnabled code reason errorType text stablePrefix mutableSuffix providerEventKind providerStability
 sent dropped received frames sentFrames droppedFrames receivedFrames consumedSamples sentSamples queuedSamples queuedMs bufferedAmount
 sampleRate channelCount echoCancellation noiseSuppression autoGainControl elapsedMs durationMs latencyMs firstChunkMs firstAudioMs
 contextTime endContextTime outputTime outputContextTime clockSkewMs mode status phase accepted complete cancelled available
 rms noise noiseFloor threshold vad probability vadThreshold onsetGate eouThreshold speechMs silenceMs confirmMs active
 segmentId inputId invokeId sayId clauseId commandId commandType profileId generation configRevision
 revision contextVersion trigger highPriority admitted count queueDepth bytes samples totalBytes totalSamples
 toolName receiptCode silent startedAt updatedAt deadlineMs fallbackMs online connected audioContextState
 settled draining paused blocked audioFinished audioCancelled inputActive workState pendingClauses captureActive
 contextState playbackBlocked hasClause started role micSentFrames micDroppedFrames micSentSamples micDroppedSamples receivedSamples
 accepting queuedFrames speech confirmTargetMs silenceTargetMs timeoutMs incomplete pending running queuedClauses hasTranscript endpointPending speechCandidate confirmedSpeech prepareCount firstAudio timestamp
 exceptionType partial stableText mutableText kind eventKind stability consumed sentBytes receivedBytes microphoneState
 windowMs windowFrames vadPassFrames rmsPassFrames qualifiedFrames maxQualifiedRunMs rmsMin rmsMax thresholdMin thresholdMax maxConfirmMs
 resetRmsCount resetVadCount resetBothCount resetUnavailableCount lastResetReason lastResetFromMs
 vadStatus vadReason vadWarmupFrames vadUnavailableFrames vadErrorFrames operation finalizing
 backendOnline connectionState recording truncation error droppedEvents instanceId source`.split(/\s+/));
const encoder=new TextEncoder();
export const debugByteLength=(value:unknown):number=>encoder.encode(JSON.stringify(value)).byteLength;
export function sanitizeDebugDetails(raw:unknown):Record<string,DebugScalar> {
  if(!raw||typeof raw!=='object'||Array.isArray(raw))return {};
  const result:Record<string,DebugScalar>={};
  for(const [key,value] of Object.entries(raw)) {
    if(!fields.has(key))continue;
    if(typeof value==='string')result[key]=value.slice(0,8000);
    else if(value===null||typeof value==='boolean'||(typeof value==='number'&&Number.isFinite(value)))result[key]=value;
  }
  return result;
}
export function sanitizeDebugEvent(raw:unknown):LiveDebugEvent|null {
  if(!raw||typeof raw!=='object'||Array.isArray(raw))return null;
  const value=raw as LiveDebugEvent;
  if(!['backend','pet','main'].includes(value.source)||!['info','warning','error'].includes(value.level)
    ||!DEBUG_STAGES.includes(value.stage)||!Number.isSafeInteger(value.seq)||value.seq<0
    ||!Number.isFinite(value.timestamp)||value.timestamp<0
    ||typeof value.instanceId!=='string'||!value.instanceId||value.instanceId.length>128
    ||typeof value.captureId!=='string'||!value.captureId||value.captureId.length>128
    ||typeof value.name!=='string'||!value.name||value.name.length>128)return null;
  const result:LiveDebugEvent={source:value.source,seq:value.seq,instanceId:value.instanceId,captureId:value.captureId,
    timestamp:value.timestamp,stage:value.stage,name:value.name,level:value.level,details:sanitizeDebugDetails(value.details)};
  for(const key of ['profileId','segmentId','inputId','invokeId','sayId','clauseId'] as const)
    if(typeof value[key]==='string')result[key]=value[key]!.slice(0,256);
  for(const key of ['generation','configRevision','id'] as const)
    if(Number.isSafeInteger(value[key])&&value[key]!>=0)result[key]=value[key];
  if(value.truncated||Object.values(value.details??{}).some(v=>typeof v==='string'&&v.length>8000))result.truncated=true;
  while(debugByteLength(result)>DEBUG_EVENT_BYTES){
    const entry=Object.entries(result.details).filter(([,v])=>typeof v==='string').sort((a,b)=>String(b[1]).length-String(a[1]).length)[0];
    if(!entry)return null;
    result.details[entry[0]]=String(entry[1]).slice(0,Math.floor(String(entry[1]).length/2));result.truncated=true;
  }
  return result;
}
export function appendDebugEvents(current:LiveDebugEvent[],incoming:LiveDebugEvent[]):LiveDebugEvent[] {
  const events=[...current,...incoming].slice(-DEBUG_MAX_EVENTS);
  let size=events.reduce((sum,event)=>sum+debugByteLength(event),0),start=0;
  while(size>DEBUG_MAX_BYTES&&start<events.length)size-=debugByteLength(events[start++]);
  return events.slice(start);
}
