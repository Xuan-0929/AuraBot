/** Carries Backend acoustic no_audio/listening state through the existing desktop status flow. */
/**
 * Owns the Pet window's single profile-bound Main Agent text/voice session. It acquires a one-shot token,
 * Localized preparation codes preserve actionable failures. It opens the minimal WebSocket protocol, captures raw 16 kHz PCM, plays the current raw TTS clause in
 * the same AudioContext, and ACKs voice only after physical completion and durable Electron history. Text delivery never requires microphone resources. Backend-confirmed speech controls interruption; cancel, overrun, replacement, and teardown share one cleanup path.
 * Visual SAY scopes carry the frozen server identity; bounded device-clock timers deliver start and output
 * envelope events to the independent player. Visual failures never participate in the durable ACK fence.
 * Source-consumption progress releases backend PCM credit; cancelled in-flight audio is drained until
 * the next ordered offer so a local clause failure cannot tear down an otherwise healthy session.
 * Capture requests echo cancellation/noise suppression without automatic gain and reports only actual
 * numeric/boolean audio settings to backend diagnostics; device identifiers remain local.
 * Opt-in scalar debug events observe these existing boundaries, sampling PCM on existing messages only;
 * capture/session fences and isolated callbacks keep diagnostics independent of voice and durable ACKs.
 * Recognition failures use the existing public error channel to request a repeat of unconfirmed speech.
 * Realtime engine mode disables typed sends and accepts current-clause text/avatar amendments while
 * audio plays. The final amended text still waits for physical completion before durable history.
 */

import type { DebugScalar, LiveDebugEvent } from "../shared/contracts/liveDebug";
import {SPEECH_TEXT} from "../features/settings/speechText";
import type {DisplayLanguage} from "../i18n";
import type { AvatarCue } from "../shared/contracts/avatar";
import type { PerformanceEvent } from "../features/live2d/performance";

const SUBPROTOCOL = "aurabot.main-agent.v1";
const DEFAULT_GATEWAY_URL = "http://127.0.0.1:6401";
const MAX_SOCKET_BACKLOG = 128 * 1024;

export type MainAgentRealtimeState = "off" | "connecting" | "active" | "error";
export interface VoiceProfile { profileId: string; revision?: number; }
export interface RealtimeToken {token:string;profile_id:string;revision:number;generation:number;}
export interface MainAgentRealtimeClientOptions {
  gatewayUrl?: string;
  displayLanguage?:()=>DisplayLanguage;
  getToken?: () => Promise<RealtimeToken>;
  onDebug?: (event: LiveDebugEvent) => void;
  onPerformance?: (event: PerformanceEvent) => void;
  onState?: (state: MainAgentRealtimeState, detail?: string) => void;
  onVoiceState?: (active:boolean) => void;
  onClause?: (text: string | null) => void;
  onMessage?: (entry: {profileId:string;generation:number;id:string;role:"user"|"assistant";content:string}) => Promise<void>;
  onAgentState?: (state: "idle"|"no_audio"|"listening"|"thinking"|"speaking")=>void;
}

interface CurrentClause { receivedSamples:number; consumedSamples:number; id: string; text:string; settled: boolean; cancelled: boolean; draining?:boolean; cue?:AvatarCue; mode:"text"|"voice"; profileId:string; serverGeneration:number; started:boolean; startContextTime?:number; startDeadline?:number; }
interface WorkletMessage {
  type: "frame" | "clause-complete" | "clause-start" | "playback-level" | "playback-progress" | "overrun";
  samples?: Int16Array;
  clauseId?: string;
  endContextTime?: number;
  contextTime?: number;
  level?:number;
  consumedSamples?:number;
}

export class MainAgentRealtimeClient {
  private readonly gatewayUrl: string;
  private readonly onState: NonNullable<MainAgentRealtimeClientOptions["onState"]>;
  private readonly onClause: NonNullable<MainAgentRealtimeClientOptions["onClause"]>;
  private state: MainAgentRealtimeState = "off";
  private socket: WebSocket | null = null;
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private microphoneSettings: Record<string, number | boolean> = {};
  private playback: AudioWorkletNode | null = null;
  private generation = 0;
  private serverGeneration = 0;
  private configRevision = 0;
  private cueTimer:ReturnType<typeof setTimeout>|null=null;
  private levelTimer:ReturnType<typeof setTimeout>|null=null;
  private levels:Array<{contextTime:number;level:number}>=[];
  readonly visualDiagnostics={dropped:0,failures:0,starts:0,clockSkewMs:0};
  private playbackBlocked = true;
  private clause: CurrentClause | null = null;
  private discardAudio = false;
  private voice = false;
  private voiceMode:"pipeline"|"realtime"="pipeline";
  private toggling = false;
  private profile:VoiceProfile|null=null;
  private ready:Promise<void>|null=null;
  private readyResolve:(()=>void)|null=null;
  private readyReject:((error:Error)=>void)|null=null;
  private controls:Promise<void>=Promise.resolve();
  private pending=new Map<string,{resolve:()=>void;reject:(error:Error)=>void;timer:ReturnType<typeof setTimeout>;debugCaptureId:string|null}>();
  private readonly debugInstanceId = crypto.randomUUID();
  private debugCaptureId:string|null=null;
  private debugSeq=0;
  private debugLastSample=-Infinity;
  private micSentFrames=0;
  private micDroppedFrames=0;
  private micSentSamples=0;
  private micDroppedSamples=0;
  get voiceEnabled():boolean {return this.voice;}


  constructor(private readonly options: MainAgentRealtimeClientOptions = {}) {
    this.gatewayUrl = options.gatewayUrl ?? DEFAULT_GATEWAY_URL;
    this.onState = options.onState ?? (() => undefined);
    this.onClause = options.onClause ?? (() => undefined);
  }

  setDebugCapture(captureId:string|null):void {
    if(this.debugCaptureId===captureId)return;
    this.debugCaptureId=captureId;
    if(!captureId)return;
    this.debugLastSample=-Infinity;
    this.micSentFrames=this.micDroppedFrames=this.micSentSamples=this.micDroppedSamples=0;
    this.debug("transport","transport.snapshot",{state:this.state});
    this.debug("capture","capture.snapshot",{state:this.stream?"running":"off",voiceEnabled:this.voice,contextState:this.context?.state??"absent",...this.microphoneSettings});
    this.debug("devicePlayback","devicePlayback.snapshot",{state:this.clause?(this.playbackBlocked?"paused":this.clause.draining?"draining":this.clause.started?"playing":"queued"):"idle",playbackBlocked:this.playbackBlocked,hasClause:!!this.clause,started:this.clause?.started??false,draining:this.clause?.draining??false,...this.queueStats()});
  }

  private debug(stage:string,name:string,details:Record<string,DebugScalar>,level:LiveDebugEvent["level"]="info",clause=this.clause):void {
    if(!this.debugCaptureId)return;
    const event:LiveDebugEvent={source:"pet",instanceId:this.debugInstanceId,seq:++this.debugSeq,captureId:this.debugCaptureId,timestamp:Date.now(),stage,name,level,
      ...(this.profile?{profileId:clause?.profileId??this.profile.profileId}:{}),
      ...(this.serverGeneration?{generation:clause?.serverGeneration??this.serverGeneration}:{}),
      ...(this.configRevision?{configRevision:this.configRevision}:{}),
      ...(clause?{clauseId:clause.id,...(clause.cue?{sayId:clause.cue.say_id,invokeId:clause.cue.invoke_id}:{})}:{}),details};
    try{this.options.onDebug?.(event);}catch{/* Diagnostics never affect realtime delivery. */}
  }
  private queueStats():Record<string,number> {
    const receivedSamples=this.clause?.receivedSamples??0,consumedSamples=this.clause?.consumedSamples??0;
    const queuedSamples=Math.max(0,receivedSamples-consumedSamples);
    return {receivedSamples,consumedSamples,queuedSamples,queuedMs:queuedSamples/24};
  }
  private sampleAudio(stage:"capture"|"devicePlayback"):void {
    if(!this.debugCaptureId)return;
    const now=performance.now();if(now-this.debugLastSample<500)return;this.debugLastSample=now;
    this.debug(stage,"audio.stats",{micSentFrames:this.micSentFrames,micDroppedFrames:this.micDroppedFrames,micSentSamples:this.micSentSamples,micDroppedSamples:this.micDroppedSamples,bufferedAmount:this.socket?.bufferedAmount??0,...this.queueStats()});
  }
  private async writeHistory(entry:Parameters<NonNullable<MainAgentRealtimeClientOptions["onMessage"]>>[0]):Promise<void> {
    const captureId=this.debugCaptureId,generation=this.generation,clause=this.clause;
    const started=captureId?performance.now():0;
    if(captureId)this.debug("devicePlayback","history.write.started",{role:entry.role},"info",clause);
    try{
      await this.options.onMessage?.(entry);
      if(captureId&&captureId===this.debugCaptureId&&generation===this.generation)this.debug("devicePlayback","history.write.completed",{role:entry.role,elapsedMs:performance.now()-started},"info",clause);
    }catch(error){
      if(captureId&&captureId===this.debugCaptureId&&generation===this.generation)this.debug("devicePlayback","history.write.failed",{role:entry.role,code:"history_write_failed",errorType:debugErrorType(error),elapsedMs:performance.now()-started},"error",clause);
      throw error;
    }
  }

  async start(profile: VoiceProfile): Promise<void> {
    if(this.profile&&(this.profile.profileId!==profile.profileId||this.profile.revision!==profile.revision))await this.stop();
    if(this.socket?.readyState===WebSocket.OPEN && this.state==="active")return;
    if(this.ready)return this.ready;
    this.profile={...profile};
    this.serverGeneration=0;this.configRevision=profile.revision??0;
    this.controls=Promise.resolve();
    this.playbackBlocked=true;
    const generation=++this.generation;
    this.setState("connecting");
    this.ready=new Promise<void>((resolve,reject)=>{this.readyResolve=resolve;this.readyReject=reject;});
    const waiting=this.ready;
    const timeout=setTimeout(()=>{if(generation===this.generation)void this.fail("连接超时","connection_timeout");},15000);
    const debugCaptureId=this.debugCaptureId;
    void (async()=>{
      try {
        let body:Partial<RealtimeToken>;
        if(this.options.getToken)body=await this.options.getToken();
        else {
          const response=await fetch(`${this.gatewayUrl}/api/v1/main-agent/realtime-token`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({profileId:profile.profileId,revision:profile.revision??0}),signal:AbortSignal.timeout(10000)});
          if(!response.ok)throw new Error(`无法建立会话 (${response.status})`);
          body=await response.json() as Partial<RealtimeToken>;
        }
        if(typeof body.token!=="string"||!body.token||body.profile_id!==profile.profileId||!Number.isInteger(body.generation)||!Number.isInteger(body.revision))throw new Error("无效或过期会话凭据");
        if(profile.revision!==undefined&&body.revision!==profile.revision)throw new Error("Profile 配置版本已变化");
        if(generation===this.generation){this.serverGeneration=body.generation!;this.configRevision=body.revision!;this.openSocket(body.token,generation);}
      }catch(error){if(generation===this.generation)await this.fail(error,"connection_failed",debugCaptureId);}
    })();
    try {await waiting;}finally{clearTimeout(timeout);if(this.ready===waiting)this.ready=null;}
  }
  async submitText(text:string, commandId=crypto.randomUUID()):Promise<void> {
    if(!this.profile)throw new Error("桌宠尚未建立会话");
    await this.start(this.profile);
    if(this.voice&&this.voiceMode==='realtime')throw new Error(publicError('realtime_text_disabled',this.options.displayLanguage?.()));
    await this.command("text.submit",{text},commandId);
  }
  async stop():Promise<void> {
    const generation=++this.generation;
    this.rejectPending(new Error("会话已关闭"));
    await this.teardown(true);
    if(generation===this.generation)this.setState("off");
  }
  async toggle(profile:VoiceProfile):Promise<void> {
    if(this.toggling)return;
    this.toggling=true;
    try {
      await this.start(profile);
      const generation=this.generation;
      if(this.voice){
        this.voice=false;this.options.onVoiceState?.(false);this.interruptClause();await this.stopAudio();
        if(generation===this.generation)await this.command("voice.stop");
      }else{
        try {
          await this.startAudio(generation);
          if(generation!==this.generation)return;
          await this.command("voice.start", {audio_settings:this.microphoneSettings});
          if(generation===this.generation){this.voice=true;this.options.onVoiceState?.(true);if(this.debugCaptureId)this.debug("capture","voice.started",{voiceEnabled:true});}
        } catch(error){await this.stopAudio();throw error;}
      }
    } finally {this.toggling=false;}
  }

  private command(type:string,fields:Record<string,unknown>={},id=crypto.randomUUID()):Promise<void>{
    const captureId=this.debugCaptureId,generation=this.generation;
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{if(captureId&&captureId===this.debugCaptureId&&generation===this.generation)this.debug("transport","command.timeout",{commandType:type,commandId:id,code:"command_timeout"},"error");this.pending.delete(id);reject(new Error("命令确认超时，未自动重试"));void this.fail("命令确认超时，未自动重试，请确认对话状态。","command_timeout",captureId);},12000);
      this.pending.set(id,{resolve,reject,timer,debugCaptureId:captureId});
      if(this.debugCaptureId)this.debug("transport","command.sent",{commandType:type,commandId:id});
      if(!this.send({type,command_id:id,...fields})){clearTimeout(timer);this.pending.delete(id);reject(new Error("连接已断开"));if(this.debugCaptureId)this.debug("transport","command.failed",{commandType:type,commandId:id,code:"socket_not_open"},"error");}
    });
  }
  private rejectPending(error:Error){
    this.readyReject?.(error);this.readyReject=null;this.readyResolve=null;
    for(const item of this.pending.values()){clearTimeout(item.timer);item.reject(error);}this.pending.clear();
  }

  private openSocket(token: string, generation: number): void {
    const url = new URL(this.gatewayUrl);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = "/api/v1/main-agent/realtime";
    const socket = new WebSocket(url, [SUBPROTOCOL, `aurabot.capability.${token}`]);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    socket.onopen=()=>{if(this.current(socket,generation)&&this.debugCaptureId)this.debug("transport","transport.open",{state:"open"});};
    socket.onmessage = (message) => {
      if (!this.current(socket, generation)) return;
      const debugCaptureId=this.debugCaptureId;
      // PCM must follow its offer even while an earlier history write is awaiting IPC.
      this.controls=this.controls.then(async()=>{
        if(!this.current(socket,generation))return;
        if(typeof message.data==="string")await this.handleControl(JSON.parse(message.data));
        else this.appendTts(message.data as ArrayBuffer);
      }).catch(error=>{if(this.current(socket,generation))return this.fail(error,"control_failed",debugCaptureId);});
    };
    socket.onerror = () => { if (this.current(socket, generation)) void this.fail("realtime transport error","transport_error"); };
    socket.onclose = () => { if (this.current(socket, generation)) void this.fail("realtime connection closed","transport_closed"); };
  }

  private async handleControl(event: Record<string, unknown>): Promise<void> {
    if(!this.matchesIdentity(event)){this.visualDiagnostics.dropped++;return;}
    switch (event.type) {
      case "session.ready":
        this.voiceMode=event.voice_mode==='realtime'?'realtime':'pipeline';
        this.setState("active");this.readyResolve?.();this.readyResolve=null;this.readyReject=null;
        break;
      case "playback.pause":
        this.applyGate(true);
        break;
      case "playback.resume":
        this.applyGate(false);
        break;
      case "clause.offer": {
        const id = String(event.clause_id ?? "");
        if (!id || this.clause) throw new Error("invalid clause offer");
        this.discardAudio = false;
        const cue=this.cueFrom(event);
        this.clause = { receivedSamples:0,consumedSamples:0,id, text:String(event.text??""), settled: false, cancelled: false, cue,
          mode:event.mode==="text"?"text":"voice",profileId:this.profile!.profileId,serverGeneration:this.serverGeneration,started:false };
        if(this.debugCaptureId)this.debug("devicePlayback","clause.offered",{state:"queued",hasClause:true,started:false,draining:false,mode:this.clause.mode,...this.queueStats()});
        if(cue)this.performance({type:"offer",cue,clauseId:id,mode:this.clause.mode});
        if(event.mode==="text") { await this.completeClause(id); }
        else { this.playback?.port.postMessage({ type: "offer", clauseId: id }); }

        break;
      }
      case "clause.text": {
        if(this.clause&&this.clause.id===event.clause_id&&!this.clause.settled&&typeof event.text==='string'&&event.text.length<=8000)this.clause.text=event.text;
        break;
      }
      case "clause.avatar": {
        const clause=this.clause;
        if(clause&&clause.id===event.clause_id&&!clause.settled&&clause.cue){
          const updated=this.cueFrom({...clause.cue,avatar:event.avatar});
          if(updated){clause.cue=updated;this.performance({type:'update',cue:updated,clauseId:clause.id,mode:clause.mode});}
        }
        break;
      }
      case "say.closed": {
        const cue=this.cueFrom(event);
        if(cue)this.performance({type:"closed",cue,clauseCount:Number.isInteger(event.clause_count)?Number(event.clause_count):undefined});
        break;
      }
      case "say.cancel": {
        const cue=this.cueFrom(event);if(cue)this.performance({type:"cancel",cue});
        break;
      }
      case "clause.audio_end": {
        const clause = this.clause;
        if (!clause && this.discardAudio) break;
        if (!clause || clause.id !== event.clause_id) throw new Error("audio_end has no current clause");
        this.playback?.port.postMessage({ type: "finish", clauseId: clause.id });
        break;
      }
      case "clause.cancel": {
        const clause = this.clause;
        if (clause && clause.id === event.clause_id) {
          if(this.debugCaptureId)this.debug("devicePlayback","clause.cancelled",{state:"idle",hasClause:false,draining:false,code:"server_cancel"});
          clause.cancelled = true;
          clause.settled = true;
          this.flushClause();
        }
        break;
      }
      case "command.ack": {
        const id=String(event.command_id??"");const item=this.pending.get(id);
        if(item&&item.debugCaptureId&&item.debugCaptureId===this.debugCaptureId)this.debug("transport",event.status==="accepted"?"command.accepted":"command.rejected",{commandId:id,status:event.status==="accepted"?"accepted":"rejected",...(event.status==="accepted"?{}:{code:debugCode(event.code,"command_rejected")})},event.status==="accepted"?"info":"warning");
        if(item){clearTimeout(item.timer);this.pending.delete(id);event.status==="accepted"?item.resolve():item.reject(new Error(publicError(String(event.code??"请求被拒绝"),this.options.displayLanguage?.())));}
        break;
      }
      case "user.message":
        await this.writeHistory({id:String(event.message_id),role:"user",content:String(event.text),profileId:this.profile!.profileId,generation:this.serverGeneration});
        break;
      case "agent.state":
        if(["idle","no_audio","listening","thinking","speaking"].includes(String(event.state)))this.options.onAgentState?.(event.state as "idle"|"no_audio"|"listening"|"thinking"|"speaking");
        break;
      case "session.error":
        await this.fail(publicError(String(event.code ?? "session error"),this.options.displayLanguage?.()),debugCode(event.code,"session_error"));
        break;
      default:
        break;
    }
  }

  private async startAudio(generation: number): Promise<void> {
    if (this.context || generation !== this.generation) return;
    const captureId=this.debugCaptureId;
    if(captureId)this.debug("capture","capture.starting",{state:"starting"});
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: false },
      });
      if (generation !== this.generation) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream=stream;
      const settings = stream.getTracks()[0]?.getSettings?.();
      this.microphoneSettings = {};
      for(const key of ["sampleRate", "channelCount", "echoCancellation", "noiseSuppression", "autoGainControl"] as const){
        const value = settings?.[key];
        if(typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value)))this.microphoneSettings[key]=value;
      }
      if(captureId&&captureId===this.debugCaptureId)this.debug("capture","capture.settings",this.microphoneSettings);
      for(const track of stream.getTracks())track.addEventListener("ended",()=>{
        if(generation===this.generation&&this.stream===stream)void this.fail("麦克风已断开，请检查设备后重新开启语音。","microphone_ended");
      },{once:true});
      const context = new AudioContext();
      this.context=context;
      await context.audioWorklet.addModule(new URL("./audio-worklet.js", import.meta.url));
      if (generation !== this.generation) {
        stream.getTracks().forEach((track) => track.stop());
        await context.close();
        return;
      }
      const mic = new AudioWorkletNode(context, "aurabot-mic-capture");
      const playback = new AudioWorkletNode(context, "aurabot-tts-playback", { outputChannelCount: [1] });
      mic.port.onmessage = (message: MessageEvent<WorkletMessage>) => this.handleMic(message.data, generation);
      playback.port.onmessage = (message: MessageEvent<WorkletMessage>) => {if(generation===this.generation&&this.playback===playback)this.handlePlayback(message.data);};
      context.createMediaStreamSource(stream).connect(mic);
      // A destination connection keeps the capture processor in the active pull graph; it emits silence.
      mic.connect(context.destination);
      for(const node of [mic,playback])node.onprocessorerror=()=>{if(generation===this.generation&&this.context===context)void this.fail("音频处理失败，请重新开启语音。","audio_processor_error");};
      playback.connect(context.destination);
      await context.resume();
      if(generation!==this.generation){stream.getTracks().forEach(track=>track.stop());if(context.state!=="closed")await context.close();return;}
      this.stream = stream;
      this.context = context;
      this.playback = playback;
      if(this.debugCaptureId)this.debug("capture","capture.started",{state:"running",contextState:context.state,...this.microphoneSettings});
      this.applyGate(false);
      this.setState("active");
    } catch (error) {
      if(captureId&&captureId===this.debugCaptureId&&generation===this.generation)this.debug("capture","capture.failed",{code:"capture_failed",errorType:debugErrorType(error)},"error");
      if(generation===this.generation)await this.stopAudio();
      throw error;
    }
  }

  private handleMic(message: WorkletMessage, generation: number): void {
    if (generation !== this.generation || !this.voice) return;
    if (message.type === "frame" && message.samples) {
      const socket = this.socket;
      if (socket?.readyState === WebSocket.OPEN && socket.bufferedAmount <= MAX_SOCKET_BACKLOG) {
        const bytes = new Uint8Array(new ArrayBuffer(message.samples.byteLength));
        bytes.set(new Uint8Array(
          message.samples.buffer, message.samples.byteOffset, message.samples.byteLength,
        ));
        socket.send(bytes.buffer);
        if(this.debugCaptureId){this.micSentFrames++;this.micSentSamples+=message.samples.length;}
      }else if(this.debugCaptureId){this.micDroppedFrames++;this.micDroppedSamples+=message.samples.length;}
      this.sampleAudio("capture");
    }
  }

  private appendTts(encoded: ArrayBuffer): void {
    if (this.discardAudio) return;
    if (!this.clause || this.clause.settled || encoded.byteLength === 0 || encoded.byteLength % 2 !== 0) {
      void this.fail("TTS PCM has no active clause or is malformed");
      return;
    }
    this.clause.receivedSamples+=encoded.byteLength/2;this.sampleAudio("devicePlayback");
    const samples = new Int16Array(encoded);
    this.playback?.port.postMessage({ type: "append", samples }, [samples.buffer]);
  }

  private handlePlayback(message: WorkletMessage): void {
    const clause=this.clause;
    if(message.type==="playback-progress"){
      if(clause&&!clause.settled&&message.clauseId===clause.id&&Number.isSafeInteger(message.consumedSamples)&&message.consumedSamples!>=0){
        clause.consumedSamples=message.consumedSamples!;this.sampleAudio("devicePlayback");
        this.send({type:"renderer.progress",clause_id:clause.id,consumed_samples:message.consumedSamples});
      }
      return;
    }
    if(message.type==="clause-start"&&clause&&message.clauseId===clause.id&&!clause.started&&!clause.settled){
      if(!Number.isFinite(message.contextTime)){this.visualDiagnostics.dropped++;return;}
      if(clause.startContextTime===undefined){if(this.debugCaptureId)this.debug("devicePlayback","devicePlayback.consumed.started",{contextTime:message.contextTime!});clause.startContextTime=message.contextTime;clause.startDeadline=Date.now()+1500;this.scheduleStart(clause);}return;
    }
    if(message.type==="playback-level"){
      if(clause&&message.clauseId===clause.id&&!clause.settled&&!this.playbackBlocked&&Number.isFinite(message.level)&&Number.isFinite(message.contextTime)){
        this.levels.push({contextTime:message.contextTime!,level:Math.max(0,Math.min(1,message.level!))});
        if(this.levels.length>8){this.levels.shift();this.visualDiagnostics.dropped++;}
        if(this.levelTimer===null)this.scheduleLevel(clause);
      }
      return;
    }
    if (message.type === "clause-complete" && message.clauseId) {
      const generation=this.generation,debugCaptureId=this.debugCaptureId;
      if(message.endContextTime===undefined){void this.fail("音频输出时钟不可用");return;}
      void this.completeClause(message.clauseId,message.endContextTime).catch(error=>{if(generation===this.generation)return this.fail(error,"playback_failed",debugCaptureId);});
    }
    else if (message.type === "overrun" && clause && message.clauseId === clause.id){if(this.debugCaptureId)this.debug("devicePlayback","devicePlayback.overrun",{code:"playback_overrun",...this.queueStats()},"warning");this.interruptClause();}
  }

  private async completeClause(clauseId: string, endContextTime?:number): Promise<void> {
    const clause = this.clause;
    if (!clause || clause.id !== clauseId || clause.settled || clause.draining) return;
    const generation=this.generation;
    if(endContextTime!==undefined){
      if(!Number.isFinite(endContextTime))throw new Error("无效音频播放时钟");
      clause.draining=true;
      const context=this.context;const deadline=Date.now()+10000;
      while(context&&(context.getOutputTimestamp().contextTime??0)<endContextTime){
        if(this.clause!==clause||clause.settled||generation!==this.generation)return;
        if(Date.now()>deadline||context.state!=="running")throw new Error("音频输出已停止，回复未确认交付。");
        await new Promise(resolve=>setTimeout(resolve,20));
      }
      if(!context||this.clause!==clause||clause.settled||generation!==this.generation)return;
    }
    if(endContextTime!==undefined&&this.debugCaptureId)this.debug("devicePlayback","devicePlayback.physical.completed",{state:"completed",contextTime:endContextTime,...this.queueStats()});
    clause.settled = true;
    await this.writeHistory({id:clause.id,role:"assistant",content:clause.text,profileId:clause.profileId,generation:clause.serverGeneration});
    if(generation!==this.generation||this.clause!==clause||clause.cancelled)return;
    this.onClause(clause.text);
    if(clause.mode==="text"&&clause.cue)this.performance({type:"start",cue:clause.cue,clauseId:clause.id,mode:"text",text:clause.text});
    const ackSent=this.send({ type: "renderer.ack", clause_id: clause.id, complete: true });
    if(ackSent&&this.debugCaptureId)this.debug("devicePlayback","renderer.ack.sent",{state:"idle",hasClause:false,draining:false,complete:true});
    if(clause.cue)this.performance({type:"complete",cue:clause.cue,clauseId:clause.id,mode:clause.mode});
    this.performance({type:"level",level:0});this.clearCueTimer();this.clearLevels();
    if(this.clause===clause)this.clause=null;
  }

  private interruptClause(): void {
    const clause = this.clause;
    if (clause && !clause.settled && !clause.cancelled) {
      clause.settled = true;
      const ackSent=this.send({ type: "renderer.ack", clause_id: clause.id, complete: false });
      if(this.debugCaptureId){this.debug("devicePlayback","clause.interrupted",{state:"idle",hasClause:false,draining:false,code:"local_interrupt"},"warning");if(ackSent)this.debug("devicePlayback","renderer.ack.sent",{complete:false});}
    }
    this.flushClause();
  }

  private flushClause(flush = true): void {
    this.discardAudio = true;
    this.clearCueTimer();this.clearLevels();if(this.clause?.cue)this.performance({type:"cancel",cue:this.clause.cue,clauseId:this.clause.id});this.performance({type:"level",level:0});
    if (flush) this.playback?.port.postMessage({ type: "flush" });
    this.clause = null;
    this.onClause(null);
  }

  private applyGate(blocked: boolean): void {
    if (blocked !== this.playbackBlocked) {
      this.playbackBlocked = blocked;
      if(this.debugCaptureId)this.debug("devicePlayback",blocked?"devicePlayback.paused":"devicePlayback.resumed",{state:blocked?"paused":this.clause?"playing":"idle",playbackBlocked:blocked});
      const transition = blocked ? "pause" : "resume";
      this.playback?.port.postMessage({ type: transition });this.performance({type:transition});
      if(transition==="pause"){this.clearCueTimer();this.clearLevels();}else if(this.clause&&!this.clause.started)this.scheduleStart(this.clause);
    }
  }

  private send(event: Record<string, unknown>): boolean {
    const socket = this.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(event));
    return true;
  }

  private current(socket: WebSocket, generation: number): boolean {
    return this.socket === socket && this.generation === generation;
  }

  private async fail(error: unknown,code="realtime_failed",debugCaptureId=this.debugCaptureId): Promise<void> {
    if(debugCaptureId&&debugCaptureId===this.debugCaptureId)this.debug("transport","transport.failed",{code,errorType:debugErrorType(error)},"error");
    const generation=++this.generation;
    this.rejectPending(new Error(error instanceof Error?error.message:String(error)));
    await this.teardown(false);
    if(generation===this.generation)this.setState("error", error instanceof Error ? error.message : String(error));
  }

  private async teardown(sendPartial: boolean): Promise<void> {
    if (sendPartial) this.interruptClause();
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      socket.close();
    }
    this.voice=false;this.options.onVoiceState?.(false);this.clearCueTimer();this.clearLevels();this.performance({type:"reset"});
    this.options.onAgentState?.("idle");
    this.clause=null;
    this.discardAudio=false;
    this.onClause(null);
    await this.stopAudio();
  }
  private async stopAudio():Promise<void>{
    const hadAudio=!!(this.stream||this.context);
    this.microphoneSettings = {};
    this.clearCueTimer();this.clearLevels();this.performance({type:"level",level:0});
    const stream=this.stream;this.stream=null;stream?.getTracks().forEach(track=>track.stop());
    if(this.debugCaptureId&&hadAudio)this.debug("capture","voice.stopped",{voiceEnabled:false,state:"off",micSentFrames:this.micSentFrames,micDroppedFrames:this.micDroppedFrames,micSentSamples:this.micSentSamples,micDroppedSamples:this.micDroppedSamples});
    this.playback?.port.postMessage({type:"flush"});this.playback=null;
    const context=this.context;this.context=null;if(context&&context.state!=="closed")await context.close();
  }

  private matchesIdentity(event:Record<string,unknown>):boolean {
    return (event.profile_id===undefined||event.profile_id===this.profile?.profileId)
      &&(event.generation===undefined||event.generation===this.serverGeneration)
      &&(event.config_revision===undefined||event.config_revision===this.configRevision);
  }
  private cueFrom(event:Record<string,unknown>):AvatarCue|undefined {
    if(event.profile_id!==this.profile?.profileId||event.generation!==this.serverGeneration||event.config_revision!==this.configRevision||typeof event.say_id!=="string"||!event.say_id||typeof event.call_id!=="string"||typeof event.invoke_id!=="string")return undefined;
    const avatar=event.avatar&&typeof event.avatar==='object'&&!Array.isArray(event.avatar)?event.avatar as Record<string,unknown>:{};
    return {profile_id:this.profile!.profileId,generation:this.serverGeneration,config_revision:this.configRevision,say_id:event.say_id,call_id:event.call_id,invoke_id:event.invoke_id,
      avatar:{...(typeof avatar.motion==='string'?{motion:avatar.motion}:{}),...(typeof avatar.expression==='string'?{expression:avatar.expression}:{})}};
  }
  private performance(event:PerformanceEvent):void {try{this.options.onPerformance?.(event);}catch{this.visualDiagnostics.failures++;}}
  private clearCueTimer():void {if(this.cueTimer!==null){clearTimeout(this.cueTimer);this.cueTimer=null;}}
  private clearLevels():void {if(this.levelTimer!==null)clearTimeout(this.levelTimer);this.levelTimer=null;this.levels=[];}
  private scheduleLevel(clause:CurrentClause):void {
    const generation=this.generation;const deadline=Date.now()+1500;
    const check=()=>{
      this.levelTimer=null;
      if(generation!==this.generation||this.clause!==clause||clause.settled||this.playbackBlocked||!this.context){this.clearLevels();return;}
      const outputTime=this.context.getOutputTimestamp().contextTime;
      if(typeof outputTime!=='number'||!Number.isFinite(outputTime)||Date.now()>deadline){this.clearLevels();this.performance({type:"level",level:0});return;}
      let latest:number|undefined;
      while(this.levels.length&&this.levels[0].contextTime<=outputTime)latest=this.levels.shift()!.level;
      if(latest!==undefined)this.performance({type:"level",cue:clause.cue,clauseId:clause.id,level:latest});
      if(this.levels.length)this.levelTimer=setTimeout(check,Math.max(5,Math.min(50,(this.levels[0].contextTime-outputTime)*1000)));
    };
    check();
  }
  private scheduleStart(clause:CurrentClause):void {
    this.clearCueTimer();
    if(clause.startContextTime===undefined||clause.started||clause.settled||this.playbackBlocked||!this.context)return;
    const generation=this.generation;
    const check=()=>{
      this.cueTimer=null;
      if(generation!==this.generation||this.clause!==clause||clause.started||clause.settled||this.playbackBlocked||!this.context)return;
      const outputTime=this.context.getOutputTimestamp().contextTime;
      if(typeof outputTime!=='number'||!Number.isFinite(outputTime)||Date.now()>(clause.startDeadline??0)){this.visualDiagnostics.dropped++;return;}
      const remaining=clause.startContextTime!-outputTime;
      if(remaining>0.001){this.cueTimer=setTimeout(check,Math.max(5,Math.min(50,remaining*1000)));return;}
      clause.started=true;this.visualDiagnostics.starts++;this.visualDiagnostics.clockSkewMs=Math.max(0,-remaining*1000);
      if(this.debugCaptureId)this.debug("devicePlayback","devicePlayback.physical.started",{state:"playing",contextTime:outputTime,clockSkewMs:this.visualDiagnostics.clockSkewMs});
      if(clause.cue)this.performance({type:"start",cue:clause.cue,clauseId:clause.id,mode:"voice",text:clause.text});
    };
    check();
  }

  private setState(state: MainAgentRealtimeState, detail?: string): void {
    this.state = state;
    if(this.debugCaptureId)this.debug("transport","transport.state",{state});
    this.onState(state, detail);
  }
}

export function publicError(code:string,language:DisplayLanguage="zh"):string {
  if(code.endsWith("_optional_dependency_missing"))code="speech_optional_dependency_missing";
  const speech=SPEECH_TEXT[language];
  if(Object.prototype.hasOwnProperty.call(speech,code))return speech[code as keyof typeof speech];
  const messages:Record<string,string>={
    text_model_unavailable:"Main Agent 模型尚未配置，请在设置中保存并应用。",
    main_model_error:"Main Agent 请求失败，请检查模型配置和网络后重试。",
    stt_transcription_failed:language==="zh"?"这句话没有识别完整，请再说一遍。":"Speech recognition did not complete. Please say that again.",
    runtime_unavailable:"Backend 尚未就绪，请稍后重试。",
    backend_restarting:"正在应用配置，请等待 Backend 重新就绪。"
  };
  return messages[code]??code;
}

// Only known error classes and machine codes enter diagnostics; messages may contain secrets.
function debugErrorType(error:unknown):string {
  const name=error instanceof Error?error.name:"UnknownError";
  return ["Error","TypeError","RangeError","SyntaxError","AbortError","NotAllowedError","NotFoundError","NotReadableError","OverconstrainedError","InvalidStateError","SecurityError"].includes(name)?name:"UnknownError";
}
function debugCode(value:unknown,fallback:string):string {
  return typeof value==="string"&&/^[a-z][a-z0-9_]{0,79}$/.test(value)?value:fallback;
}
