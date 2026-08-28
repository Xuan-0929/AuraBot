/**
 * Drives the standalone aurabot.main-agent.v1 protocol lab. It acquires one-shot capabilities, owns the
 * browser session/control sequence, reuses the formal Renderer worklets for local pause and physical-cursor
 * ACK, renders the authoritative event timeline, and exposes deliberate protocol faults/manual commands.
 */

import {
  ConnectionScope,
  FaultController,
  SharedInitializer,
  TtsFrameTracker,
  encodeMicFrame,
  parseTtsFrame,
} from "./protocol";

const protocol = "aurabot.main-agent.v1";
const gatewayInput = element<HTMLInputElement>("gateway");
const tokenInput = element<HTMLInputElement>("token");
const statusNode = element<HTMLElement>("status");
const timeline = element<HTMLOListElement>("timeline");
const textInput = element<HTMLTextAreaElement>("text");
const clauseNode = element<HTMLElement>("clause");
const faults = new FaultController();
const socketScope = new ConnectionScope<WebSocket>();
const playbackInitializer = new SharedInitializer<AudioWorkletNode>();
const ttsTracker = new TtsFrameTracker();

let socket: WebSocket | null = null;
let sequence = 0;
let rendererEpoch = 0;
let mode: "voice" | "text" = "voice";
let clauseId: string | null = null;
let micStreamId = 1;
let micFrameSequence = 0;
let micSampleStart = 0n;
let micDiscontinuity = false;
let micContext: AudioContext | null = null;
let micStream: MediaStream | null = null;
let playbackContext: AudioContext | null = null;
let playbackNode: AudioWorkletNode | null = null;
let localCandidatePaused = false;
let serverPlaybackBlocked = true;

element<HTMLButtonElement>("acquire").onclick = acquireToken;
element<HTMLButtonElement>("connect").onclick = connect;
element<HTMLButtonElement>("submit-text").onclick = () => command("text.submit", { text: textInput.value });
element<HTMLButtonElement>("candidate").onclick = () => {
  serverPlaybackBlocked = true;
  syncPlaybackGate();
  command("speech.candidate");
};
element<HTMLButtonElement>("rejected").onclick = () => command("speech.rejected");
element<HTMLButtonElement>("confirmed").onclick = () => command("speech.confirmed", { transcript: textInput.value, complete: true });
element<HTMLButtonElement>("voice-mode").onclick = () => setMode("voice");
element<HTMLButtonElement>("text-mode").onclick = () => setMode("text");
element<HTMLButtonElement>("mic-start").onclick = startMic;
element<HTMLButtonElement>("mic-stop").onclick = () => void stopMic();
element<HTMLButtonElement>("ack-complete").onclick = () => ackClause(true);
element<HTMLButtonElement>("ack-partial").onclick = () => ackClause(false);
element<HTMLButtonElement>("clear").onclick = () => timeline.replaceChildren();

document.querySelectorAll<HTMLButtonElement>("[data-fault]").forEach((button) => {
  button.onclick = () => {
    const fault = button.dataset.fault as "gap" | "duplicate" | "badEpoch" | "backpressure";
    if (fault === "backpressure") {
      sendBackpressureBurst();
    } else {
      faults.arm(fault);
      record("fault.armed", fault);
    }
  };
});

async function acquireToken(): Promise<void> {
  try {
    const response = await fetch(`${gatewayInput.value}/api/v1/main-agent/realtime-token`, { method: "POST" });
    if (!response.ok) throw new Error(`token request returned ${response.status}`);
    const body = await response.json() as { token: string; expires_in_ms: number };
    tokenInput.value = body.token;
    record("token.acquired", `expires in ${body.expires_in_ms} ms`);
  } catch (error) {
    record("token.error", message(error));
  }
}

function connect(): void {
  if (!tokenInput.value) {
    record("connect.error", "Acquire or paste a capability first.");
    return;
  }
  if (socket) {
    socket.onopen = null;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;
    socket.close();
  }
  void stopMic(false);
  sequence = 0;
  rendererEpoch = 0;
  const url = new URL(gatewayInput.value);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/api/v1/main-agent/realtime";
  const nextSocket = new WebSocket(url, [protocol, `aurabot.capability.${tokenInput.value}`]);
  socket = nextSocket;
  socketScope.install(nextSocket);
  nextSocket.binaryType = "arraybuffer";
  serverPlaybackBlocked = true;
  localCandidatePaused = false;
  ttsTracker.reset();
  setStatus("connecting");
  nextSocket.onopen = () => {
    if (!socketScope.isCurrent(nextSocket)) return;
    sendControlTo(nextSocket, {
      type: "session.open",
      command_id: crypto.randomUUID(),
      client_id: crypto.randomUUID(),
      mode,
    });
  };
  nextSocket.onmessage = (event) => {
    if (!socketScope.isCurrent(nextSocket)) return;
    if (typeof event.data === "string") {
      handleControl(nextSocket, JSON.parse(event.data) as Record<string, unknown>);
    } else {
      void handleTts(nextSocket, event.data as ArrayBuffer);
    }
  };
  nextSocket.onerror = () => {
    if (socketScope.isCurrent(nextSocket)) record("socket.error", "WebSocket transport error");
  };
  nextSocket.onclose = (event) => {
    if (!socketScope.clear(nextSocket)) return;
    socket = null;
    serverPlaybackBlocked = true;
    localCandidatePaused = false;
    setStatus("offline");
    record("socket.closed", `${event.code} ${event.reason}`.trim());
    void stopMic(false);
    playbackNode?.port.postMessage({ type: "flush" });
    ttsTracker.reset();
  };
}

function handleControl(owner: WebSocket, event: Record<string, unknown>): void {
  record(String(event.type), event);
  if (event.type === "session.opened") {
    rendererEpoch = Number(event.renderer_epoch);
    mode = event.mode === "text" ? "text" : "voice";
    setStatus("online");
  } else if (event.type === "state.snapshot") {
    rendererEpoch = Number(event.renderer_epoch);
    mode = event.mode === "text" ? "text" : "voice";
    serverPlaybackBlocked = mode !== "voice" || event.playback_blocked !== false;
    syncPlaybackGate();
  } else if (event.type === "playback.pause") {
    serverPlaybackBlocked = true;
    syncPlaybackGate();
  } else if (event.type === "playback.resume") {
    serverPlaybackBlocked = false;
    syncPlaybackGate();
  } else if (event.type === "clause.offer") {
    clauseId = String(event.clause_id);
    clauseNode.textContent = JSON.stringify(event, null, 2);
  } else if (event.type === "clause.cancel" || event.type === "playback.stop") {
    if (event.type === "clause.cancel" && event.clause_id === clauseId) clauseId = null;
    playbackNode?.port.postMessage({ type: "flush" });
    ttsTracker.cancelActive();
    if (
      event.type === "playback.stop"
      && (event.reason === "speech_candidate" || event.reason === "mode_switch")
    ) {
      serverPlaybackBlocked = true;
    }
  } else if (event.type === "session.replaced") {
    serverPlaybackBlocked = true;
    localCandidatePaused = false;
    playbackNode?.port.postMessage({ type: "flush" });
    ttsTracker.reset();
    clauseId = null;
    if (socketScope.clear(owner)) socket = null;
    void stopMic(false);
    setStatus("replaced");
  }
}

async function startMic(): Promise<void> {
  if (mode !== "voice") await setMode("voice");
  command("mic.start");
  if (micContext) return;
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true } });
    micContext = new AudioContext();
    await micContext.audioWorklet.addModule(new URL("./mic-worklet.js", import.meta.url));
    const source = micContext.createMediaStreamSource(micStream);
    const capture = new AudioWorkletNode(micContext, "aurabot-mic-capture");
    capture.port.onmessage = (event: MessageEvent<{
      type: "frame" | "candidate" | "released";
      samples?: Int16Array;
    }>) => {
      if (event.data.type === "frame" && event.data.samples) sendMicSamples(event.data.samples);
      if (event.data.type === "candidate") localCandidatePaused = true;
      if (event.data.type === "released") localCandidatePaused = false;
      syncPlaybackGate();
    };
    source.connect(capture);
    record("mic.capture", `${micContext.sampleRate} Hz browser → 16000 Hz protocol`);
  } catch (error) {
    record("mic.error", message(error));
    command("mic.stop");
  }
}

async function stopMic(send = true): Promise<void> {
  if (send && socket?.readyState === WebSocket.OPEN) command("mic.stop");
  micStream?.getTracks().forEach((track) => track.stop());
  micStream = null;
  const context = micContext;
  micContext = null;
  micStreamId += 1;
  micFrameSequence = 0;
  micSampleStart = 0n;
  micDiscontinuity = false;
  if (context) await context.close();
}

function sendMicSamples(samples: Int16Array): void {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  if (socket.bufferedAmount > 128 * 1024) {
    if (!micDiscontinuity) record("mic.overrun", "transport backlog; dropping until writable");
    micDiscontinuity = true;
    micFrameSequence += 1;
    micSampleStart += 320n;
    return;
  }
  for (const wireSequence of faults.nextFrameSequence(micFrameSequence)) {
    const encoded = encodeMicFrame(samples, {
      streamId: micStreamId,
      frameSequence: wireSequence,
      sampleStart: micSampleStart,
      discontinuity: micDiscontinuity,
    });
    socket.send(encoded);
  }
  micDiscontinuity = false;
  micFrameSequence += 1;
  micSampleStart += 320n;
}

function sendBackpressureBurst(): void {
  const silence = new Int16Array(320);
  for (let index = 0; index < 80; index += 1) sendMicSamples(silence);
  record("fault.sent", "80-frame mic burst");
}

async function handleTts(owner: WebSocket, encoded: ArrayBuffer): Promise<void> {
  try {
    const frame = parseTtsFrame(encoded);
    const ordering = ttsTracker.accept(frame);
    if (ordering === "duplicate") {
      record("tts.duplicate", `${frame.streamId}:${frame.frameSequence}`);
      return;
    }
    const node = await ensurePlayback();
    if (!socketScope.isCurrent(owner) || !clauseId) return;
    node.port.postMessage({
      type: "append",
      clauseId,
      samples: frame.samples,
      final: frame.final,
    }, [frame.samples.buffer]);
    syncPlaybackGate();
  } catch (error) {
    record("tts.error", message(error));
    ttsTracker.cancelActive();
    playbackNode?.port.postMessage({ type: "flush" });
  }
}

async function ensurePlayback(): Promise<AudioWorkletNode> {
  return playbackInitializer.get(async () => {
    const context = new AudioContext();
    playbackContext = context;
    await context.audioWorklet.addModule(new URL("./playback-worklet.js", import.meta.url));
    const node = new AudioWorkletNode(context, "aurabot-tts-playback", { outputChannelCount: [1] });
    playbackNode = node;
    node.port.onmessage = (event: MessageEvent<{ type: string; clauseId?: string }>) => {
      if (event.data.type === "overrun") {
        record("tts.overrun", "playback queue flushed");
        ackClause(false);
      } else if (event.data.type === "clause-complete" && event.data.clauseId === clauseId) {
        ackClause(true);
      }
    };
    node.connect(context.destination);
    return node;
  });
}

async function setMode(next: "voice" | "text"): Promise<void> {
  if (next === mode) return;
  if (next === "text") await stopMic(false);
  serverPlaybackBlocked = true;
  playbackNode?.port.postMessage({ type: "flush" });
  ttsTracker.cancelActive();
  command("mode.set", { renderer_epoch: faults.rendererEpoch(rendererEpoch), mode: next });
  mode = next;
}

function ackClause(complete: boolean): void {
  if (!clauseId) {
    record("renderer.ack.skipped", "No current clause.");
    return;
  }
  command("renderer.ack", {
    renderer_epoch: faults.rendererEpoch(rendererEpoch),
    clause_id: clauseId,
    complete,
  });
  clauseId = null;
  clauseNode.textContent = "No active clause.";
}

function syncPlaybackGate(): void {
  playbackNode?.port.postMessage({
    type: localCandidatePaused || serverPlaybackBlocked ? "pause" : "resume",
  });
}

function command(type: string, fields: Record<string, unknown> = {}): void {
  sendControl({ type, command_id: crypto.randomUUID(), ...fields });
}

function sendControl(event: Record<string, unknown>): void {
  if (!socket) {
    record("send.skipped", "Socket is not open.");
    return;
  }
  sendControlTo(socket, event);
}

function sendControlTo(owner: WebSocket, event: Record<string, unknown>): void {
  if (!socketScope.isCurrent(owner) || owner.readyState !== WebSocket.OPEN) {
    record("send.skipped", "Socket is not open.");
    return;
  }
  const value = { ...event, seq: ++sequence };
  owner.send(JSON.stringify(value));
  record(`→ ${String(event.type)}`, value);
}

function setStatus(state: string): void {
  statusNode.textContent = state;
  statusNode.dataset.state = state === "online" ? "online" : "offline";
}

function record(type: string, detail: unknown): void {
  const item = document.createElement("li");
  const timestamp = document.createElement("time");
  timestamp.textContent = new Date().toLocaleTimeString();
  item.append(timestamp, `${type} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
  timeline.prepend(item);
}

function element<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`missing test client element: ${id}`);
  return node as T;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
