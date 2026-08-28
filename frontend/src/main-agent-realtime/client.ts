/**
 * Owns the Pet window's single profile-bound Main Agent voice session. It acquires a one-shot token,
 * opens the minimal WebSocket protocol, captures raw 16 kHz PCM, plays the current raw TTS clause in
 * the same AudioContext, and ACKs only the physical playback cursor. Local RMS and server VAD gates
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

interface CurrentClause { id: string; settled: boolean; cancelled: boolean; }
interface WorkletMessage {
  type: "frame" | "candidate" | "released" | "clause-complete" | "overrun";
  samples?: Int16Array;
  clauseId?: string;
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

  constructor(options: MainAgentRealtimeClientOptions = {}) {
    this.gatewayUrl = options.gatewayUrl ?? DEFAULT_GATEWAY_URL;
    this.onState = options.onState ?? (() => undefined);
    this.onClause = options.onClause ?? (() => undefined);
  }

  get currentState(): MainAgentRealtimeState { return this.state; }

  async start(profile: VoiceProfile): Promise<void> {
    if (this.state === "connecting" || this.state === "active") return;
    const generation = ++this.generation;
    this.gate = new PlaybackGate();
    this.setState("connecting");
    try {
      const response = await fetch(`${this.gatewayUrl}/api/v1/main-agent/realtime-token`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(profile),
      });
      if (!response.ok) throw new Error(`realtime token returned HTTP ${response.status}`);
      const body = await response.json() as { token?: unknown };
      if (typeof body.token !== "string" || !body.token) throw new Error("invalid realtime token");
      if (generation === this.generation) this.openSocket(body.token, generation);
    } catch (error) {
      if (generation === this.generation) await this.fail(error);
    }
  }

  async stop(): Promise<void> {
    ++this.generation;
    await this.teardown(true);
    this.setState("off");
  }

  async toggle(profile: VoiceProfile): Promise<void> {
    if (this.state === "off" || this.state === "error") await this.start(profile);
    else await this.stop();
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
      if (typeof message.data === "string") {
        try {
          void this.handleControl(
            JSON.parse(message.data) as Record<string, unknown>, generation,
          ).catch((error) => this.fail(error));
        }
        catch (error) { void this.fail(error); }
      } else this.appendTts(message.data as ArrayBuffer);
    };
    socket.onerror = () => { if (this.current(socket, generation)) void this.fail("realtime transport error"); };
    socket.onclose = () => { if (this.current(socket, generation)) void this.fail("realtime connection closed"); };
  }

  private async handleControl(event: Record<string, unknown>, generation: number): Promise<void> {
    switch (event.type) {
      case "session.ready":
        await this.startAudio(generation);
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
        this.clause = { id, settled: false, cancelled: false };
        this.playback?.port.postMessage({ type: "offer", clauseId: id });
        this.onClause(String(event.text ?? ""));
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
      case "session.error":
        await this.fail(String(event.code ?? "session error"));
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
      const context = new AudioContext();
      await context.audioWorklet.addModule(new URL("./audio-worklet.js", import.meta.url));
      if (generation !== this.generation) {
        stream.getTracks().forEach((track) => track.stop());
        await context.close();
        return;
      }
      const mic = new AudioWorkletNode(context, "aurabot-mic-capture");
      const playback = new AudioWorkletNode(context, "aurabot-tts-playback", { outputChannelCount: [1] });
      mic.port.onmessage = (message: MessageEvent<WorkletMessage>) => this.handleMic(message.data, generation);
      playback.port.onmessage = (message: MessageEvent<WorkletMessage>) => this.handlePlayback(message.data);
      context.createMediaStreamSource(stream).connect(mic);
      playback.connect(context.destination);
      await context.resume();
      this.stream = stream;
      this.context = context;
      this.playback = playback;
      this.applyGate(this.gate.setServerBlocked(false));
      this.setState("active");
    } catch (error) {
      await this.fail(error);
    }
  }

  private handleMic(message: WorkletMessage, generation: number): void {
    if (generation !== this.generation) return;
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
    if (message.type === "clause-complete" && message.clauseId) this.completeClause(message.clauseId);
    else if (message.type === "overrun") this.interruptClause();
  }

  private completeClause(clauseId: string): void {
    const clause = this.clause;
    if (!clause || clause.id !== clauseId || clause.settled) return;
    clause.settled = true;
    this.send({ type: "renderer.ack", clause_id: clause.id, complete: true });
    this.flushClause(false);
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
    ++this.generation;
    await this.teardown(false);
    this.setState("error", error instanceof Error ? error.message : String(error));
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
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.playback?.port.postMessage({ type: "flush" });
    this.playback = null;
    const context = this.context;
    this.context = null;
    this.clause = null;
    this.onClause(null);
    if (context) await context.close();
  }

  private setState(state: MainAgentRealtimeState, detail?: string): void {
    this.state = state;
    this.onState(state, detail);
  }
}
