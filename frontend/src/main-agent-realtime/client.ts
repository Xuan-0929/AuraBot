/**
 * Owns the Pet window's single profile-bound Main Agent text/voice session. It acquires a one-shot token,
 * opens the minimal WebSocket protocol, captures raw 16 kHz PCM, plays the current raw TTS clause in
 * the same AudioContext, and ACKs voice only after physical completion and durable Electron history. Text delivery never requires microphone resources. Local RMS and server VAD gates
 * pause without dropping audio; cancel, overrun, replacement, and teardown share one cleanup path.
 */

const SUBPROTOCOL = "aurabot.main-agent.v1";
const DEFAULT_GATEWAY_URL = "http://127.0.0.1:6401";
const MAX_SOCKET_BACKLOG = 128 * 1024;

export type MainAgentRealtimeState = "off" | "connecting" | "active" | "error";
export interface VoiceProfile { profileId: string; soul: string; }
export interface MainAgentRealtimeClientOptions {
  gatewayUrl?: string;
  onState?: (state: MainAgentRealtimeState, detail?: string) => void;
  onClause?: (text: string | null) => void;
  onMessage?: (entry: {id:string;role:"user"|"assistant";content:string}) => Promise<void>;
  onAgentState?: (state: "idle"|"listening"|"thinking"|"speaking")=>void;
}

export class PlaybackGate {
  private localPaused = false;
  private serverPaused = true;

  setLocalCandidate(value: boolean): "pause" | "resume" | null {
    return this.change(() => { this.localPaused = value; });
  }

  setServerBlocked(value: boolean): "pause" | "resume" | null {
    return this.change(() => { this.serverPaused = value; });
  }

  get blocked(): boolean { return this.localPaused || this.serverPaused; }

  private change(update: () => void): "pause" | "resume" | null {
    const before = this.blocked;
    update();
    return before === this.blocked ? null : this.blocked ? "pause" : "resume";
  }
}

interface CurrentClause { id: string; text:string; settled: boolean; cancelled: boolean; draining?:boolean; }
interface WorkletMessage {
  type: "frame" | "candidate" | "released" | "clause-complete" | "overrun";
  samples?: Int16Array;
  clauseId?: string;
  endContextTime?: number;
}

export class MainAgentRealtimeClient {
  private readonly gatewayUrl: string;
  private readonly onState: NonNullable<MainAgentRealtimeClientOptions["onState"]>;
  private readonly onClause: NonNullable<MainAgentRealtimeClientOptions["onClause"]>;
  private state: MainAgentRealtimeState = "off";
  private socket: WebSocket | null = null;
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private playback: AudioWorkletNode | null = null;
  private generation = 0;
  private gate = new PlaybackGate();
  private clause: CurrentClause | null = null;
  private voice = false;
  private toggling = false;
  private profile:VoiceProfile|null=null;
  private ready:Promise<void>|null=null;
  private readyResolve:(()=>void)|null=null;
  private readyReject:((error:Error)=>void)|null=null;
  private controls:Promise<void>=Promise.resolve();
  private pending=new Map<string,{resolve:()=>void;reject:(error:Error)=>void;timer:ReturnType<typeof setTimeout>}>();
  get voiceEnabled():boolean {return this.voice;}


  constructor(private readonly options: MainAgentRealtimeClientOptions = {}) {
    this.gatewayUrl = options.gatewayUrl ?? DEFAULT_GATEWAY_URL;
    this.onState = options.onState ?? (() => undefined);
    this.onClause = options.onClause ?? (() => undefined);
  }

  get currentState(): MainAgentRealtimeState { return this.state; }

  async start(profile: VoiceProfile): Promise<void> {
    if(this.socket?.readyState===WebSocket.OPEN && this.state==="active")return;
    if(this.ready)return this.ready;
    this.profile=profile;
    this.controls=Promise.resolve();
    this.gate=new PlaybackGate();
    const generation=++this.generation;
    this.setState("connecting");
    this.ready=new Promise<void>((resolve,reject)=>{this.readyResolve=resolve;this.readyReject=reject;});
    const waiting=this.ready;
    const timeout=setTimeout(()=>{if(generation===this.generation)void this.fail("连接超时");},15000);
    void (async()=>{
      try {
        const response=await fetch(`${this.gatewayUrl}/api/v1/main-agent/realtime-token`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(profile),signal:AbortSignal.timeout(10000)});
        if(!response.ok)throw new Error(`无法建立会话 (${response.status})`);
        const body=await response.json() as {token?:unknown};
        if(typeof body.token!=="string"||!body.token)throw new Error("无效会话凭据");
        if(generation===this.generation)this.openSocket(body.token,generation);
      }catch(error){if(generation===this.generation)await this.fail(error);}
    })();
    try {await waiting;}finally{clearTimeout(timeout);if(this.ready===waiting)this.ready=null;}
  }
  async submitText(text:string, commandId=crypto.randomUUID()):Promise<void> {
    if(!this.profile)throw new Error("桌宠尚未建立会话");
    await this.start(this.profile);
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
        this.voice=false;this.interruptClause();await this.stopAudio();
        if(generation===this.generation)await this.command("voice.stop");
      }else{
        try {
          await this.startAudio(generation);
          if(generation!==this.generation)return;
          await this.command("voice.start");
          if(generation===this.generation)this.voice=true;
        } catch(error){await this.stopAudio();throw error;}
      }
    } finally {this.toggling=false;}
  }

  private command(type:string,fields:Record<string,unknown>={},id=crypto.randomUUID()):Promise<void>{
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error("命令确认超时，未自动重试"));void this.fail("命令确认超时，未自动重试，请确认对话状态。");},12000);
      this.pending.set(id,{resolve,reject,timer});
      if(!this.send({type,command_id:id,...fields})){clearTimeout(timer);this.pending.delete(id);reject(new Error("连接已断开"));}
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
    socket.onmessage = (message) => {
      if (!this.current(socket, generation)) return;
      // PCM must follow its offer even while an earlier history write is awaiting IPC.
      this.controls=this.controls.then(async()=>{
        if(!this.current(socket,generation))return;
        if(typeof message.data==="string")await this.handleControl(JSON.parse(message.data),generation);
        else this.appendTts(message.data as ArrayBuffer);
      }).catch(error=>{if(this.current(socket,generation))return this.fail(error);});
    };
    socket.onerror = () => { if (this.current(socket, generation)) void this.fail("realtime transport error"); };
    socket.onclose = () => { if (this.current(socket, generation)) void this.fail("realtime connection closed"); };
  }

  private async handleControl(event: Record<string, unknown>, generation: number): Promise<void> {
    switch (event.type) {
      case "session.ready":
        this.setState("active");this.readyResolve?.();this.readyResolve=null;this.readyReject=null;
        break;
      case "playback.pause":
        this.applyGate(this.gate.setServerBlocked(true));
        break;
      case "playback.resume":
        this.applyGate(this.gate.setServerBlocked(false));
        break;
      case "clause.offer": {
        const id = String(event.clause_id ?? "");
        if (!id || this.clause) throw new Error("invalid clause offer");
        this.clause = { id, text:String(event.text??""), settled: false, cancelled: false };
        if(event.mode==="text") { await this.completeClause(id); }
        else { this.playback?.port.postMessage({ type: "offer", clauseId: id }); }

        break;
      }
      case "clause.audio_end": {
        const clause = this.clause;
        if (!clause || clause.id !== event.clause_id) throw new Error("audio_end has no current clause");
        this.playback?.port.postMessage({ type: "finish", clauseId: clause.id });
        break;
      }
      case "clause.cancel": {
        const clause = this.clause;
        if (clause && clause.id === event.clause_id) {
          clause.cancelled = true;
          clause.settled = true;
          this.flushClause();
        }
        break;
      }
      case "command.ack": {
        const id=String(event.command_id??"");const item=this.pending.get(id);
        if(item){clearTimeout(item.timer);this.pending.delete(id);event.status==="accepted"?item.resolve():item.reject(new Error(publicError(String(event.code??"请求被拒绝"))));}
        break;
      }
      case "user.message":
        await this.options.onMessage?.({id:String(event.message_id),role:"user",content:String(event.text)});
        break;
      case "agent.state":
        if(["idle","listening","thinking","speaking"].includes(String(event.state)))this.options.onAgentState?.(event.state as "idle"|"listening"|"thinking"|"speaking");
        break;
      case "session.error":
        await this.fail(publicError(String(event.code ?? "session error")));
        break;
      default:
        break;
    }
  }

  private async startAudio(generation: number): Promise<void> {
    if (this.context || generation !== this.generation) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true },
      });
      if (generation !== this.generation) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream=stream;
      for(const track of stream.getTracks())track.addEventListener("ended",()=>{
        if(generation===this.generation&&this.stream===stream)void this.fail("麦克风已断开，请检查设备后重新开启语音。");
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
      for(const node of [mic,playback])node.onprocessorerror=()=>{if(generation===this.generation&&this.context===context)void this.fail("音频处理失败，请重新开启语音。");};
      playback.connect(context.destination);
      await context.resume();
      if(generation!==this.generation){stream.getTracks().forEach(track=>track.stop());if(context.state!=="closed")await context.close();return;}
      this.stream = stream;
      this.context = context;
      this.playback = playback;
      this.applyGate(this.gate.setServerBlocked(false));
      this.setState("active");
    } catch (error) {
      if(generation===this.generation)await this.stopAudio();
      throw error;
    }
  }

  private handleMic(message: WorkletMessage, generation: number): void {
    if (generation !== this.generation || !this.voice) return;
    if (message.type === "candidate") this.applyGate(this.gate.setLocalCandidate(true));
    else if (message.type === "released") this.applyGate(this.gate.setLocalCandidate(false));
    else if (message.type === "frame" && message.samples) {
      const socket = this.socket;
      if (socket?.readyState === WebSocket.OPEN && socket.bufferedAmount <= MAX_SOCKET_BACKLOG) {
        const bytes = new Uint8Array(new ArrayBuffer(message.samples.byteLength));
        bytes.set(new Uint8Array(
          message.samples.buffer, message.samples.byteOffset, message.samples.byteLength,
        ));
        socket.send(bytes.buffer);
      }
    }
  }

  private appendTts(encoded: ArrayBuffer): void {
    if (!this.clause || this.clause.settled || encoded.byteLength === 0 || encoded.byteLength % 2 !== 0) {
      void this.fail("TTS PCM has no active clause or is malformed");
      return;
    }
    const samples = new Int16Array(encoded);
    this.playback?.port.postMessage({ type: "append", samples }, [samples.buffer]);
  }

  private handlePlayback(message: WorkletMessage): void {
    if (message.type === "clause-complete" && message.clauseId) {
      const generation=this.generation;
      if(message.endContextTime===undefined){void this.fail("音频输出时钟不可用");return;}
      void this.completeClause(message.clauseId,message.endContextTime).catch(error=>{if(generation===this.generation)return this.fail(error);});
    }
    else if (message.type === "overrun") this.interruptClause();
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
    clause.settled = true;
    await this.options.onMessage?.({id:clause.id,role:"assistant",content:clause.text});
    if(generation!==this.generation||this.clause!==clause||clause.cancelled)return;
    this.onClause(clause.text);
    this.send({ type: "renderer.ack", clause_id: clause.id, complete: true });
    if(this.clause===clause)this.clause=null;
  }

  private interruptClause(): void {
    const clause = this.clause;
    if (clause && !clause.settled && !clause.cancelled) {
      clause.settled = true;
      this.send({ type: "renderer.ack", clause_id: clause.id, complete: false });
    }
    this.flushClause();
  }

  private flushClause(flush = true): void {
    if (flush) this.playback?.port.postMessage({ type: "flush" });
    this.clause = null;
    this.onClause(null);
  }

  private applyGate(transition: "pause" | "resume" | null): void {
    if (transition) this.playback?.port.postMessage({ type: transition });
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

  private async fail(error: unknown): Promise<void> {
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
    this.voice=false;
    this.options.onAgentState?.("idle");
    this.clause=null;
    this.onClause(null);
    await this.stopAudio();
  }
  private async stopAudio():Promise<void>{
    const stream=this.stream;this.stream=null;stream?.getTracks().forEach(track=>track.stop());
    this.playback?.port.postMessage({type:"flush"});this.playback=null;
    const context=this.context;this.context=null;if(context&&context.state!=="closed")await context.close();
  }

  private setState(state: MainAgentRealtimeState, detail?: string): void {
    this.state = state;
    this.onState(state, detail);
  }
}

function publicError(code:string):string {
  const messages:Record<string,string>={
    text_model_unavailable:"Main Agent 模型尚未配置，请在设置中保存并应用。",
    speech_unavailable:"语音不可用，请检查语音开关、密钥、SDK、模型和许可证文件。",
    main_model_error:"Main Agent 请求失败，请检查模型配置和网络后重试。",
    runtime_unavailable:"Backend 尚未就绪，请稍后重试。",
    backend_restarting:"正在应用配置，请等待 Backend 重新就绪。"
  };
  return messages[code]??code;
}
