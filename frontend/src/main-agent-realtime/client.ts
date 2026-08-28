/**
 * Owns the formal Pet window's aurabot.main-agent.v1 voice session from one mic-button gesture through
 * token acquisition, WebSocket lease, 16 kHz microphone upload, clause-tagged TTS playback, and physical
 * cursor ACK. It combines an immediate local energy candidate with the backend's authoritative playback
 * block, keeps paused PCM queued, and flushes only for cancel/stop/overrun/session teardown. The Console
 * text chat remains outside this module, and motion/expression fields are intentionally not interpreted.
 */

import {
  TtsFrameTracker,
  encodeMicFrame,
  parseTtsFrame,
} from "../realtime-test-client/protocol";

const SUBPROTOCOL = "aurabot.main-agent.v1";
const DEFAULT_GATEWAY_URL = "http://127.0.0.1:6401";
const MAX_SOCKET_BACKLOG = 128 * 1024;

export type MainAgentRealtimeState = "off" | "connecting" | "active" | "error";

export interface MainAgentRealtimeClientOptions {
  gatewayUrl?: string;
  onState?: (state: MainAgentRealtimeState, detail?: string) => void;
  onClause?: (text: string | null) => void;
}

export class PlaybackGate {
  private localCandidatePaused = false;
  private serverPlaybackBlocked = false;

  constructor(serverPlaybackBlocked = false) {
    this.serverPlaybackBlocked = serverPlaybackBlocked;
  }

  setLocalCandidate(value: boolean): "pause" | "resume" | null {
    const before = this.blocked;
    this.localCandidatePaused = value;
    return this.transition(before);
  }

  setServerBlocked(value: boolean): "pause" | "resume" | null {
    const before = this.blocked;
    this.serverPlaybackBlocked = value;
    return this.transition(before);
  }

  get blocked(): boolean {
    return this.localCandidatePaused || this.serverPlaybackBlocked;
  }

  private transition(before: boolean): "pause" | "resume" | null {
    if (before === this.blocked) return null;
    return this.blocked ? "pause" : "resume";
  }
}

interface CurrentClause {
  id: string;
  text: string;
  settled: boolean;
}

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
  private sequence = 0;
  private readonly pendingCommands = new Map<string, string>();
  private rendererEpoch = 0;
  private generation = 0;
  private micStreamId = 1;
  private micFrameSequence = 0;
  private micSampleStart = 0n;
  private micDiscontinuity = false;
  private micStream: MediaStream | null = null;
  private micContext: AudioContext | null = null;
  private playbackContext: AudioContext | null = null;
  private playbackNode: AudioWorkletNode | null = null;
  private playbackPending: Promise<AudioWorkletNode | null> | null = null;
  private gate = new PlaybackGate(true);
  private readonly ttsTracker = new TtsFrameTracker();
  private clause: CurrentClause | null = null;

  constructor(options: MainAgentRealtimeClientOptions = {}) {
    this.gatewayUrl = options.gatewayUrl ?? DEFAULT_GATEWAY_URL;
    this.onState = options.onState ?? (() => undefined);
    this.onClause = options.onClause ?? (() => undefined);
  }

  get currentState(): MainAgentRealtimeState {
    return this.state;
  }

  async start(): Promise<void> {
    if (this.state === "connecting" || this.state === "active") return;
    const generation = ++this.generation;
    this.resetSessionCoordinates();
    this.gate = new PlaybackGate(true);
    this.setState("connecting");
    try {
      const response = await fetch(`${this.gatewayUrl}/api/v1/main-agent/realtime-token`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(`realtime token returned HTTP ${response.status}`);
      const body = await response.json() as { token?: unknown };
      if (typeof body.token !== "string" || !body.token) {
        throw new Error("realtime token response is invalid");
      }
      if (generation !== this.generation) return;
      this.openSocket(body.token, generation);
    } catch (error) {
      if (generation === this.generation) await this.fail(error);
    }
  }

  async stop(): Promise<void> {
    ++this.generation;
    await this.teardown(true);
    this.setState("off");
  }

  async toggle(): Promise<void> {
    if (this.state === "off" || this.state === "error") await this.start();
    else await this.stop();
  }

  private openSocket(token: string, generation: number): void {
    const url = new URL(this.gatewayUrl);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = "/api/v1/main-agent/realtime";
    const socket = new WebSocket(url, [SUBPROTOCOL, `aurabot.capability.${token}`]);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    socket.onopen = () => {
      if (!this.isCurrent(socket, generation)) return;
      this.sendControl({
        type: "session.open",
        command_id: crypto.randomUUID(),
        client_id: crypto.randomUUID(),
        mode: "voice",
      });
    };
    socket.onmessage = (message) => {
      if (!this.isCurrent(socket, generation)) return;
      if (typeof message.data === "string") {
        try {
          void this.handleControl(JSON.parse(message.data) as Record<string, unknown>, generation);
        } catch (error) {
          void this.fail(error);
        }
      } else {
        void this.handleTts(message.data as ArrayBuffer, generation);
      }
    };
    socket.onerror = () => {
      if (this.isCurrent(socket, generation)) void this.fail(new Error("realtime transport error"));
    };
    socket.onclose = () => {
      if (this.isCurrent(socket, generation)) void this.fail(new Error("realtime connection closed"));
    };
  }

  private async handleControl(event: Record<string, unknown>, generation: number): Promise<void> {
    switch (event.type) {
      case "session.opened":
        this.rendererEpoch = Number(event.renderer_epoch);
        await this.startMicrophone(generation);
        break;
      case "state.snapshot":
        this.rendererEpoch = Number(event.renderer_epoch);
        this.applyGate(this.gate.setServerBlocked(event.playback_blocked !== false));
        break;
      case "playback.pause":
        this.applyGate(this.gate.setServerBlocked(true));
        break;
      case "playback.resume":
        this.applyGate(this.gate.setServerBlocked(false));
        break;
      case "clause.offer": {
        const id = String(event.clause_id);
        const text = String(event.text ?? "");
        this.clause = { id, text, settled: false };
        this.onClause(text);
        break;
      }
      case "clause.cancel":
        if (this.clause?.id === event.clause_id) this.cancelClause();
        break;
      case "command.ack": {
        const commandId = String(event.command_id ?? "");
        const commandType = this.pendingCommands.get(commandId);
        this.pendingCommands.delete(commandId);
        if (event.status === "rejected" && commandType === "mic.start") {
          await this.fail(new Error(`mic.start rejected: ${String(event.code ?? "unknown")}`));
        }
        break;
      }
      case "playback.stop":
        this.flushPlayback();
        break;
      case "session.replaced":
      case "session.error":
        await this.fail(new Error(String(event.type)));
        break;
      default:
        break;
    }
  }

  private async startMicrophone(generation: number): Promise<void> {
    if (this.micContext || generation !== this.generation) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true },
      });
      if (generation !== this.generation) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const context = new AudioContext();
      this.micStream = stream;
      this.micContext = context;
      await context.audioWorklet.addModule(
        new URL("../realtime-test-client/mic-worklet.js", import.meta.url),
      );
      if (generation !== this.generation) {
        stream.getTracks().forEach((track) => track.stop());
        await context.close();
        return;
      }
      const capture = new AudioWorkletNode(context, "aurabot-mic-capture");
      capture.port.onmessage = (message: MessageEvent<WorkletMessage>) => {
        if (generation !== this.generation) return;
        if (message.data.type === "frame" && message.data.samples) {
          this.sendMicFrame(message.data.samples);
        } else if (message.data.type === "candidate") {
          this.applyGate(this.gate.setLocalCandidate(true));
        } else if (message.data.type === "released") {
          this.applyGate(this.gate.setLocalCandidate(false));
        }
      };
      context.createMediaStreamSource(stream).connect(capture);
      this.sendCommand("mic.start");
      this.setState("active");
    } catch (error) {
      await this.fail(error);
    }
  }

  private sendMicFrame(samples: Int16Array): void {
    const socket = this.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    if (socket.bufferedAmount > MAX_SOCKET_BACKLOG) {
      this.micDiscontinuity = true;
      this.micFrameSequence += 1;
      this.micSampleStart += 320n;
      return;
    }
    socket.send(encodeMicFrame(samples, {
      streamId: this.micStreamId,
      frameSequence: this.micFrameSequence,
      sampleStart: this.micSampleStart,
      discontinuity: this.micDiscontinuity,
    }));
    this.micDiscontinuity = false;
    this.micFrameSequence += 1;
    this.micSampleStart += 320n;
  }

  private async handleTts(encoded: ArrayBuffer, generation: number): Promise<void> {
    try {
      const frame = parseTtsFrame(encoded);
      if (this.ttsTracker.accept(frame) === "duplicate") return;
      const clause = this.clause;
      if (!clause || clause.settled) throw new Error("TTS frame has no active clause");
      const node = await this.ensurePlayback(generation);
      if (!node || generation !== this.generation) return;
      node.port.postMessage({
        type: "append",
        clauseId: clause.id,
        samples: frame.samples,
        final: frame.final,
      }, [frame.samples.buffer]);
      this.applyGate(this.gate.blocked ? "pause" : "resume");
    } catch (error) {
      await this.fail(error);
    }
  }

  private async ensurePlayback(generation: number): Promise<AudioWorkletNode | null> {
    if (this.playbackNode) return this.playbackNode;
    if (this.playbackPending) return this.playbackPending;
    const pending = (async () => {
      const context = new AudioContext();
      this.playbackContext = context;
      await context.audioWorklet.addModule(
        new URL("../realtime-test-client/playback-worklet.js", import.meta.url),
      );
      if (generation !== this.generation) {
        if (this.playbackContext === context) this.playbackContext = null;
        await context.close();
        return null;
      }
      const node = new AudioWorkletNode(context, "aurabot-tts-playback", {
        outputChannelCount: [1],
      });
      node.port.onmessage = (message: MessageEvent<WorkletMessage>) => {
        if (message.data.type === "clause-complete" && message.data.clauseId) {
          this.completeClause(message.data.clauseId);
        } else if (message.data.type === "overrun") {
          this.interruptClause();
        }
      };
      node.connect(context.destination);
      await context.resume();
      this.playbackNode = node;
      return node;
    })().finally(() => {
      if (this.playbackPending === pending) this.playbackPending = null;
    });
    this.playbackPending = pending;
    return pending;
  }

  private completeClause(clauseId: string): void {
    const clause = this.clause;
    if (!clause || clause.id !== clauseId || clause.settled) return;
    clause.settled = true;
    this.sendCommand("renderer.ack", {
      renderer_epoch: this.rendererEpoch,
      clause_id: clause.id,
      complete: true,
    });
    this.clause = null;
    this.onClause(null);
  }

  private interruptClause(): void {
    const clause = this.clause;
    if (clause && !clause.settled) {
      clause.settled = true;
      this.sendCommand("renderer.ack", {
        renderer_epoch: this.rendererEpoch,
        clause_id: clause.id,
        complete: false,
      });
    }
    this.flushPlayback();
  }

  private cancelClause(): void {
    if (this.clause) this.clause.settled = true;
    this.flushPlayback();
  }

  private flushPlayback(): void {
    this.playbackNode?.port.postMessage({ type: "flush", reason: "renderer_control" });
    this.ttsTracker.cancelActive();
    this.clause = null;
    this.onClause(null);
  }

  private applyGate(transition: "pause" | "resume" | null): void {
    if (transition) this.playbackNode?.port.postMessage({ type: transition });
  }

  private sendCommand(type: string, fields: Record<string, unknown> = {}): void {
    const commandId = crypto.randomUUID();
    this.pendingCommands.set(commandId, type);
    if (!this.sendControl({ type, command_id: commandId, ...fields })) {
      this.pendingCommands.delete(commandId);
    }
  }

  private sendControl(event: Record<string, unknown>): boolean {
    const socket = this.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify({ ...event, seq: ++this.sequence }));
    return true;
  }

  private isCurrent(socket: WebSocket, generation: number): boolean {
    return this.socket === socket && this.generation === generation;
  }

  private async fail(error: unknown): Promise<void> {
    ++this.generation;
    await this.teardown(false);
    const detail = error instanceof Error ? error.message : String(error);
    this.setState("error", detail);
  }

  private async teardown(sendCommands: boolean): Promise<void> {
    const socket = this.socket;
    if (sendCommands && socket?.readyState === WebSocket.OPEN) {
      const clause = this.clause;
      if (clause && !clause.settled) {
        clause.settled = true;
        this.sendCommand("renderer.ack", {
          renderer_epoch: this.rendererEpoch,
          clause_id: clause.id,
          complete: false,
        });
      }
      this.sendCommand("mic.stop");
    }
    this.socket = null;
    if (socket) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      socket.close();
    }
    this.micStream?.getTracks().forEach((track) => track.stop());
    this.micStream = null;
    const micContext = this.micContext;
    const playbackContext = this.playbackContext;
    this.micContext = null;
    this.playbackContext = null;
    this.playbackNode?.port.postMessage({ type: "flush", reason: "teardown" });
    this.playbackNode = null;
    this.playbackPending = null;
    this.ttsTracker.reset();
    this.pendingCommands.clear();
    this.clause = null;
    this.onClause(null);
    await Promise.allSettled([
      micContext?.close() ?? Promise.resolve(),
      playbackContext?.close() ?? Promise.resolve(),
    ]);
    this.resetSessionCoordinates();
  }

  private resetSessionCoordinates(): void {
    this.sequence = 0;
    this.rendererEpoch = 0;
    this.micStreamId += 1;
    this.micFrameSequence = 0;
    this.micSampleStart = 0n;
    this.micDiscontinuity = false;
  }

  private setState(state: MainAgentRealtimeState, detail?: string): void {
    this.state = state;
    this.onState(state, detail);
  }
}
