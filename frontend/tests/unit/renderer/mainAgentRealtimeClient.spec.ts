/**
 * Checks first-audio timing and capture fencing through the production playback client.
 * Verifies the Pet's minimal voice-session contract at browser boundaries. The suite covers profile-bound
 * token acquisition, opt-in debug capture fencing/redaction, one shared AudioContext, raw microphone/TTS PCM, server playback gates, consumption
 * credit, redacted device settings, and physical cursor ACK. Cancellation drains old PCM without ending
 * the session; hardware/browser boundaries are controlled while the production client runs unchanged.
 * Realtime provider mode rejects typed input and accepts late transcript/avatar updates for the
 * current reply without persisting text before the physical output cursor completes.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { MainAgentRealtimeClient } from "@renderer/main-agent-realtime/client";

class FakePort {
  onmessage: ((event: MessageEvent) => void) | null = null;
  readonly sent: unknown[] = [];

  postMessage(value: unknown): void {
    this.sent.push(value);
  }
}

class FakeAudioWorkletNode {
  static readonly instances: FakeAudioWorkletNode[] = [];
  readonly port = new FakePort();

  constructor(_context: unknown, public readonly processorName: string) {
    FakeAudioWorkletNode.instances.push(this);
  }

  connect(): void {}
}

class FakeAudioContext {
  static readonly instances: FakeAudioContext[] = [];
  readonly audioWorklet = { addModule: vi.fn(async () => undefined) };
  readonly destination = {};
  state="running";
  outputTime=0;
  getOutputTimestamp(){return {contextTime:this.outputTime};}
  readonly close = vi.fn(async ():Promise<void> => undefined);
  readonly resume = vi.fn(async () => undefined);

  constructor() {
    FakeAudioContext.instances.push(this);
  }

  createMediaStreamSource(): { connect: () => void } {
    return { connect: () => undefined };
  }
}

class FakeWebSocket {
  static readonly OPEN = 1;
  static readonly instances: FakeWebSocket[] = [];
  readyState = 0;
  binaryType = "";
  bufferedAmount = 0;
  readonly sent: Array<string | ArrayBuffer> = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(public readonly url: URL, public readonly protocols: string[]) {
    FakeWebSocket.instances.push(this);
  }

  send(value: string | ArrayBuffer): void {
    this.sent.push(value);
  }

  close(): void {
    this.readyState = 3;
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  message(data: string | ArrayBuffer): void {
    this.onmessage?.({ data } as MessageEvent);
  }
}

function sentControls(socket: FakeWebSocket): Array<Record<string, unknown>> {
  return socket.sent
    .filter((value): value is string => typeof value === "string")
    .map((value) => JSON.parse(value) as Record<string, unknown>);
}

const profile={profileId:'profile_default',revision:1};
const cue={profile_id:profile.profileId,generation:7,config_revision:1,say_id:'say',call_id:'call',invoke_id:'invoke',avatar:{motion:'nod'}};
async function connected(options:ConstructorParameters<typeof MainAgentRealtimeClient>[0]={}){
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({token:'token',profile_id:profile.profileId,revision:1,generation:7})})));
 vi.stubGlobal('navigator',{mediaDevices:{getUserMedia:vi.fn(async()=>({getTracks:()=>[]}))}});
 vi.stubGlobal('WebSocket',FakeWebSocket);vi.stubGlobal('AudioContext',FakeAudioContext);vi.stubGlobal('AudioWorkletNode',FakeAudioWorkletNode);
 const onState=vi.fn(options.onState);
 const client=new MainAgentRealtimeClient({...options,onState});const connecting=client.start(profile);
 await vi.waitFor(()=>expect(FakeWebSocket.instances).toHaveLength(1));
 const socket=FakeWebSocket.instances[0];socket.open();socket.message(JSON.stringify({type:'session.ready',profile_id:profile.profileId,generation:7,config_revision:1}));await connecting;
 return {client,socket,onState};
}
async function voice(client:MainAgentRealtimeClient,socket:FakeWebSocket){
 const enabling=client.toggle(profile);
 await vi.waitFor(()=>expect(sentControls(socket).some(e=>e.type==='voice.start')).toBe(true));
 const command=sentControls(socket).find(e=>e.type==='voice.start')!;
 socket.message(JSON.stringify({type:'command.ack',command_id:command.command_id,status:'accepted'}));await enabling;
}
describe('formal Pet unified realtime client',()=>{
 it('updates streaming text and late cues before committing the physically played reply',async()=>{
   const persist=vi.fn(async()=>undefined),perform=vi.fn();
   const {client,socket}=await connected({onMessage:persist,onPerformance:perform});await voice(client,socket);
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'stream',text:'',mode:'voice',...cue,avatar:{}}));
   const playback=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')!;
   await vi.waitFor(()=>expect(playback.port.sent).toContainEqual({type:'offer',clauseId:'stream'}));
   socket.message(JSON.stringify({type:'clause.text',clause_id:'stream',text:'Final transcript'}));
   socket.message(JSON.stringify({type:'clause.avatar',clause_id:'stream',avatar:{expression:'smile'}}));
   await vi.waitFor(()=>expect(perform.mock.calls.some(([event])=>event.type==='update'&&event.cue.avatar.expression==='smile')).toBe(true));
   expect(persist).not.toHaveBeenCalled();
   playback.port.onmessage?.({data:{type:'clause-complete',clauseId:'stream',endContextTime:0}} as MessageEvent);
   await vi.waitFor(()=>expect(persist).toHaveBeenCalledWith(expect.objectContaining({content:'Final transcript'})));
   await client.stop();
 });
 it('blocks typed submissions only while Realtime voice is enabled',async()=>{
   const {client,socket}=await connected();
   socket.message(JSON.stringify({type:'session.ready',voice_mode:'realtime'}));await voice(client,socket);
   await expect(client.submitText('blocked')).rejects.toThrow();
   expect(sentControls(socket).some(e=>e.type==='text.submit')).toBe(false);
   await client.stop();
 });
 it('does not pause playback for a local loud frame and requests stable microphone gain',async()=>{
   const {client,socket}=await connected();
   vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue({getTracks:()=>[{
     addEventListener:vi.fn(),stop:vi.fn(),getSettings:()=>({sampleRate:48000,echoCancellation:true,noiseSuppression:true,autoGainControl:false,deviceId:'private-device'})
   }]} as unknown as MediaStream);
   await voice(client,socket);
   const mic=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-mic-capture')!;
   const playback=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')!;
   mic.port.onmessage?.({data:{type:'candidate'}} as MessageEvent);
   expect(playback.port.sent).not.toContainEqual({type:'pause'});
   expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:false}});
   expect(sentControls(socket).find(e=>e.type==='voice.start')?.audio_settings).toEqual({sampleRate:48000,echoCancellation:true,noiseSuppression:true,autoGainControl:false});
   await client.stop();
 });
 it('keeps overrun recovery local and discards in-flight PCM until the next offer',async()=>{
   const {client,socket,onState}=await connected();await voice(client,socket);
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'old',text:'hello',mode:'voice'}));
   const playback=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')!;
   await vi.waitFor(()=>expect(playback.port.sent).toContainEqual({type:'offer',clauseId:'old'}));
   playback.port.onmessage?.({data:{type:'overrun',clauseId:'old'}} as MessageEvent);
   socket.message(new Int16Array([1,2]).buffer);
   socket.message(JSON.stringify({type:'clause.audio_end',clause_id:'old'}));
   socket.message(JSON.stringify({type:'clause.cancel',clause_id:'old'}));
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'next',text:'next',mode:'voice'}));
   socket.message(new Int16Array([3,4]).buffer);
   await vi.waitFor(()=>expect(playback.port.sent).toContainEqual({type:'append',samples:new Int16Array([3,4])}));
   playback.port.onmessage?.({data:{type:'overrun',clauseId:'old'}} as MessageEvent);
   playback.port.onmessage?.({data:{type:'playback-progress',clauseId:'next',consumedSamples:2}} as MessageEvent);
   expect(sentControls(socket)).toContainEqual({type:'renderer.progress',clause_id:'next',consumed_samples:2});
   expect(sentControls(socket).filter(e=>e.type==='renderer.ack')).toEqual([{type:'renderer.ack',clause_id:'old',complete:false}]);
   expect(onState).toHaveBeenLastCalledWith('active',undefined);await client.stop();
 });
 afterEach(()=>{vi.unstubAllGlobals();FakeWebSocket.instances.length=0;FakeAudioWorkletNode.instances.length=0;FakeAudioContext.instances.length=0;});
 it('projects only Backend acoustic states and localizes silence',async()=>{
   const onAgentState=vi.fn();const {client,socket}=await connected({onAgentState});
   for(const state of ['no_audio','listening','no_audio'])socket.message(JSON.stringify({type:'agent.state',state}));
   await vi.waitFor(()=>expect(onAgentState.mock.calls.map(args=>args[0])).toEqual(['no_audio','listening','no_audio']));
   const {agentStatusLabel}=await import('../../../src/i18n');
   expect(['zh','en','ja'].map(lang=>agentStatusLabel(lang as any,'no_audio'))).toEqual(['无音频','No speech detected','音声未検出']);
   await client.stop();
 });
 it('connects text without microphone and awaits durable delivery before ACK',async()=>{
   let finish!:()=>void;const stored=new Promise<void>(resolve=>finish=resolve);const persist=vi.fn(()=>stored);
   const {client,socket}=await connected({onMessage:persist});expect(FakeAudioContext.instances).toHaveLength(0);
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'text-1',text:'hello',mode:'text'}));
   await vi.waitFor(()=>expect(persist).toHaveBeenCalledWith({id:'text-1',role:'assistant',content:'hello',profileId:profile.profileId,generation:7}));
   expect(sentControls(socket)).toEqual([]);finish();
   await vi.waitFor(()=>expect(sentControls(socket)).toContainEqual({type:'renderer.ack',clause_id:'text-1',complete:true}));await client.stop();
 });
 it('hides voice until physical completion then persists and ACKs once',async()=>{
   const persist=vi.fn(async()=>undefined);const visible=vi.fn();const {client,socket}=await connected({onMessage:persist,onClause:visible});await voice(client,socket);
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'voice-1',text:'heard',mode:'voice'}));
   const playback=()=>FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')!;
   await vi.waitFor(()=>expect(playback().port.sent).toContainEqual({type:'offer',clauseId:'voice-1'}));
   expect(persist).not.toHaveBeenCalled();expect(visible).not.toHaveBeenCalled();
   playback().port.onmessage?.({data:{type:'clause-complete',clauseId:'voice-1',endContextTime:0}} as MessageEvent);
   await vi.waitFor(()=>expect(sentControls(socket)).toContainEqual({type:'renderer.ack',clause_id:'voice-1',complete:true}));
   expect(persist).toHaveBeenCalledOnce();expect(visible).toHaveBeenCalledWith('heard');
   playback().port.onmessage?.({data:{type:'clause-complete',clauseId:'voice-1',endContextTime:0}} as MessageEvent);
   expect(persist).toHaveBeenCalledOnce();await client.stop();
 });
 it('never persists interrupted voice and retains text when microphone stops',async()=>{
   const persist=vi.fn(async()=>undefined);const {client,socket}=await connected({onMessage:persist});await voice(client,socket);
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'cut',text:'unheard',mode:'voice'}));
   await vi.waitFor(()=>expect(FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')?.port.sent).toContainEqual({type:'offer',clauseId:'cut'}));
   const stopping=client.toggle(profile);
   await vi.waitFor(()=>expect(sentControls(socket).some(e=>e.type==='voice.stop')).toBe(true));
   const command=sentControls(socket).find(e=>e.type==='voice.stop')!;socket.message(JSON.stringify({type:'command.ack',command_id:command.command_id,status:'accepted'}));await stopping;
   expect(persist).not.toHaveBeenCalled();expect(socket.readyState).toBe(1);expect(client.voiceEnabled).toBe(false);
   expect(sentControls(socket)).toContainEqual({type:'renderer.ack',clause_id:'cut',complete:false});await client.stop();
 });
 it('does not ACK failed history writes',async()=>{
   const {socket,onState}=await connected({onMessage:async()=>{throw new Error('disk full');}});
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'full',text:'hello',mode:'text'}));
   await vi.waitFor(()=>expect(onState).toHaveBeenLastCalledWith('error',expect.any(String)));expect(sentControls(socket).filter(e=>e.type==='renderer.ack'&&e.complete)).toHaveLength(0);
 });
 it('keeps text available when microphone permission is denied',async()=>{
   const {client,socket}=await connected();vi.stubGlobal('navigator',{mediaDevices:{getUserMedia:async()=>{throw new Error('permission denied');}}});
   await expect(client.toggle(profile)).rejects.toThrow('permission denied');expect(socket.readyState).toBe(1);expect(client.voiceEnabled).toBe(false);await client.stop();
 });
 it('orders PCM after its offer while accepted-user history is pending',async()=>{
   let finish!:()=>void;const write=new Promise<void>(resolve=>finish=resolve);
   const {client,socket,onState}=await connected({onMessage:()=>write});await voice(client,socket);
   socket.message(JSON.stringify({type:'user.message',message_id:'u',text:'hello'}));
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'v',text:'hello',mode:'voice'}));
   socket.message(new Int16Array([1,2]).buffer);finish();
   const playback=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')!;
   await vi.waitFor(()=>expect(playback.port.sent).toContainEqual({type:'append',samples:new Int16Array([1,2])}));
   expect(onState).toHaveBeenLastCalledWith('active',undefined);await client.stop();
 });
 it('does not ACK an old persisted clause onto a replacement connection',async()=>{
   let finish!:()=>void;const write=new Promise<void>(resolve=>finish=resolve);const persist=vi.fn(()=>write);
   const {client,socket}=await connected({onMessage:persist});
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'old',text:'hello',mode:'text'}));
   await vi.waitFor(()=>expect(persist).toHaveBeenCalled());await client.stop();
   const connecting=client.start(profile);await vi.waitFor(()=>expect(FakeWebSocket.instances).toHaveLength(2));
   const next=FakeWebSocket.instances[1];next.open();next.message(JSON.stringify({type:'session.ready',profile_id:profile.profileId,generation:7,config_revision:1}));await connecting;
   finish();await new Promise(resolve=>setTimeout(resolve,0));expect(sentControls(next)).toEqual([]);await client.stop();
 });
 it('waits for the output-device clock and excludes a clause interrupted while draining',async()=>{
   const persist=vi.fn(async()=>undefined);const {client,socket}=await connected({onMessage:persist});await voice(client,socket);
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'drain',text:'hello',mode:'voice'}));
   const playback=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')!;
   await vi.waitFor(()=>expect(playback.port.sent).toContainEqual({type:'offer',clauseId:'drain'}));
   playback.port.onmessage?.({data:{type:'clause-complete',clauseId:'drain',endContextTime:1}} as MessageEvent);
   await new Promise(resolve=>setTimeout(resolve,30));expect(persist).not.toHaveBeenCalled();
   await client.stop();FakeAudioContext.instances[0].outputTime=2;
   await new Promise(resolve=>setTimeout(resolve,30));expect(persist).not.toHaveBeenCalled();
 });

 it('suppresses a voice ACK cancelled while its completed history is being persisted',async()=>{
   let finish!:()=>void;const write=new Promise<void>(resolve=>finish=resolve);const persist=vi.fn(()=>write);
   const {client,socket,onState}=await connected({onMessage:persist});await voice(client,socket);
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'late',text:'heard',mode:'voice'}));
   const playback=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')!;
   await vi.waitFor(()=>expect(playback.port.sent).toContainEqual({type:'offer',clauseId:'late'}));
   playback.port.onmessage?.({data:{type:'clause-complete',clauseId:'late',endContextTime:0}} as MessageEvent);
   await vi.waitFor(()=>expect(persist).toHaveBeenCalledOnce());
   socket.message(JSON.stringify({type:'clause.cancel',clause_id:'late'}));
   await new Promise(resolve=>setTimeout(resolve,0));finish();await new Promise(resolve=>setTimeout(resolve,0));
   expect(sentControls(socket).filter(e=>e.type==='renderer.ack')).toEqual([]);expect(onState).toHaveBeenLastCalledWith('active',undefined);await client.stop();
 });

 it('does not let a slow old AudioContext shutdown overwrite a replacement session',async()=>{
   const {client,socket,onState}=await connected();await voice(client,socket);
   let finish!:()=>void;FakeAudioContext.instances[0].close.mockImplementation(()=>new Promise<void>(resolve=>finish=resolve));
   const stopping=client.stop();const connecting=client.start(profile);
   await vi.waitFor(()=>expect(FakeWebSocket.instances).toHaveLength(2));
   const next=FakeWebSocket.instances[1];next.open();next.message(JSON.stringify({type:'session.ready',profile_id:profile.profileId,generation:7,config_revision:1}));await connecting;
   finish();await stopping;expect(onState).toHaveBeenLastCalledWith('active',undefined);await client.stop();
 });

 it('emits voice cue only on actual output start, keeps pause identity and closes independently of ACK',async()=>{
   const perform=vi.fn();const {client,socket}=await connected({onPerformance:perform});await voice(client,socket);
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'timed',text:'hello',mode:'voice',...cue}));
   const playback=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')!;
   await vi.waitFor(()=>expect(playback.port.sent).toContainEqual({type:'offer',clauseId:'timed'}));
   expect(perform.mock.calls.some(([event])=>event.type==='start')).toBe(false);
   playback.port.onmessage?.({data:{type:'clause-start',clauseId:'timed',contextTime:1}} as MessageEvent);
   await new Promise(resolve=>setTimeout(resolve,20));expect(perform.mock.calls.some(([event])=>event.type==='start')).toBe(false);
   FakeAudioContext.instances[0].outputTime=1;
   await vi.waitFor(()=>expect(perform.mock.calls.filter(([event])=>event.type==='start')).toHaveLength(1));
   playback.port.onmessage?.({data:{type:'clause-start',clauseId:'timed',contextTime:1}} as MessageEvent);
   socket.message(JSON.stringify({type:'playback.pause'}));socket.message(JSON.stringify({type:'playback.resume'}));
   socket.message(JSON.stringify({type:'say.closed',clause_count:1,...cue}));
   await vi.waitFor(()=>expect(perform).toHaveBeenCalledWith(expect.objectContaining({type:'closed',clauseCount:1})));
   expect(perform.mock.calls.filter(([event])=>event.type==='start')).toHaveLength(1);await client.stop();
 });
 it('keeps text ACK independent of visual exceptions and drops old Profile offers',async()=>{
   const {client,socket}=await connected({onPerformance:()=>{throw new Error('visual unavailable');}});
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'old-profile',text:'wrong',mode:'text',...cue,profile_id:'other'}));
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'text-safe',text:'hello',mode:'text',...cue}));
   await vi.waitFor(()=>expect(sentControls(socket)).toContainEqual({type:'renderer.ack',clause_id:'text-safe',complete:true}));
   expect(sentControls(socket).some(event=>event.clause_id==='old-profile')).toBe(false);await client.stop();
 });
 it('uses the Main-issued token without another activation request',async()=>{
   const getToken=vi.fn(async()=>({token:'main-token',profile_id:profile.profileId,revision:1,generation:7}));
   const {client,socket}=await connected({getToken});
   expect(getToken).toHaveBeenCalledOnce();expect(fetch).not.toHaveBeenCalled();
   expect(socket.protocols).toContain('aurabot.capability.main-token');await client.stop();
 });
 it('aligns the bounded mouth envelope queue to output time and clears it on pause',async()=>{
   const perform=vi.fn();const {client,socket}=await connected({onPerformance:perform});await voice(client,socket);
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'mouth',text:'hello',mode:'voice',...cue}));
   const playback=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')!;
   await vi.waitFor(()=>expect(playback.port.sent).toContainEqual({type:'offer',clauseId:'mouth'}));
   playback.port.onmessage?.({data:{type:'playback-level',clauseId:'mouth',contextTime:0.1,level:0.6}} as MessageEvent);
   expect(perform.mock.calls.some(([event])=>event.type==='level'&&event.level===0.6)).toBe(false);
   FakeAudioContext.instances[0].outputTime=0.1;
   await vi.waitFor(()=>expect(perform).toHaveBeenCalledWith(expect.objectContaining({type:'level',level:0.6})));
   playback.port.onmessage?.({data:{type:'playback-level',clauseId:'mouth',contextTime:0.2,level:0.9}} as MessageEvent);
   socket.message(JSON.stringify({type:'playback.pause'}));
   await vi.waitFor(()=>expect(perform).toHaveBeenCalledWith({type:'pause'}));FakeAudioContext.instances[0].outputTime=0.3;
   await new Promise(resolve=>setTimeout(resolve,60));expect(perform.mock.calls.some(([event])=>event.type==='level'&&event.level===0.9)).toBe(false);await client.stop();
 });

 it('starts debug capture with actual snapshots and isolates failing diagnostics',async()=>{
   const onDebug=vi.fn();const {client,socket}=await connected({onDebug});
   expect(onDebug).not.toHaveBeenCalled();client.setDebugCapture('capture-a');
   expect(onDebug.mock.calls.map(([e])=>e.name)).toEqual(['transport.snapshot','capture.snapshot','devicePlayback.snapshot']);
   expect(onDebug.mock.calls[0][0]).toMatchObject({source:'pet',captureId:'capture-a',profileId:profile.profileId,generation:7,configRevision:1,details:{state:'active'}});
   onDebug.mockImplementation(()=>{throw new Error('diagnostics must not break delivery');});
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'safe',text:'secret words',mode:'text',...cue}));
   await vi.waitFor(()=>expect(sentControls(socket)).toContainEqual({type:'renderer.ack',clause_id:'safe',complete:true}));
   client.setDebugCapture(null);onDebug.mockClear();await client.stop();expect(onDebug).not.toHaveBeenCalled();
 });
 it('fences late debug history completion across capture replacement and redacts errors',async()=>{
   let finish!:()=>void;const onDebug=vi.fn();const {client,socket}=await connected({onDebug,onMessage:()=>new Promise<void>(resolve=>finish=resolve)});
   client.setDebugCapture('capture-a');
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'late-debug',text:'private words',mode:'text',...cue}));
   await vi.waitFor(()=>expect(onDebug.mock.calls.some(([e])=>e.name==='history.write.started')).toBe(true));
   client.setDebugCapture('capture-b');onDebug.mockClear();finish();
   await vi.waitFor(()=>expect(sentControls(socket)).toContainEqual({type:'renderer.ack',clause_id:'late-debug',complete:true}));
   expect(onDebug.mock.calls.some(([e])=>e.name==='history.write.completed')).toBe(false);
   vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValue(new Error('secret-token device-id private words'));
   await expect(client.toggle(profile)).rejects.toThrow('secret-token');
   expect(onDebug.mock.calls.some(([e])=>e.name==='capture.failed')).toBe(true);
   expect(JSON.stringify(onDebug.mock.calls)).not.toMatch(/secret-token|device-id|private words/);await client.stop();
 });
 it('snapshots current queue without restarting audio and ignores retired session callbacks',async()=>{
   const onDebug=vi.fn();const {client,socket}=await connected({onDebug});await voice(client,socket);
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'current',text:'hidden',mode:'voice',...cue}));
   socket.message(new Int16Array(2400).buffer);
   const playback=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')!;
   await vi.waitFor(()=>expect(playback.port.sent.some((e:any)=>e.type==='append')).toBe(true));
   client.setDebugCapture('capture-a');
   expect(onDebug.mock.calls.find(([e])=>e.name==='devicePlayback.snapshot')?.[0]).toMatchObject({clauseId:'current',sayId:'say',invokeId:'invoke',details:{receivedSamples:2400,consumedSamples:0,queuedMs:100}});
   client.setDebugCapture(null);expect(client.voiceEnabled).toBe(true);expect(FakeAudioContext.instances[0].close).not.toHaveBeenCalled();
   const staleMessage=playback.port.onmessage;await client.stop();client.setDebugCapture('capture-b');onDebug.mockClear();
   staleMessage?.({data:{type:'clause-complete',clauseId:'current',endContextTime:0}} as MessageEvent);
   expect(onDebug).not.toHaveBeenCalled();
 });
 it('does not carry first-audio timing across a capture clear or invent a later first packet',async()=>{
   const onDebug=vi.fn();const {client,socket}=await connected({onDebug});await voice(client,socket);client.setDebugCapture('before');
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'capture-clock',text:'reply',mode:'voice',...cue}));
   socket.message(new Int16Array(2400).buffer);
   await vi.waitFor(()=>expect(onDebug.mock.calls.some(([e])=>e.name==='devicePlayback.audio.first')).toBe(true));
   client.setDebugCapture('after');onDebug.mockClear();
   socket.message(new Int16Array(2400).buffer);
   const playback=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')!;
   await vi.waitFor(()=>expect(playback.port.sent.filter((e:any)=>e.type==='append')).toHaveLength(2));
   FakeAudioContext.instances[0].outputTime=0.1;
   playback.port.onmessage?.({data:{type:'clause-start',clauseId:'capture-clock',contextTime:0.1}} as MessageEvent);
   const started=onDebug.mock.calls.find(([e])=>e.name==='devicePlayback.physical.started')?.[0];
   expect(started.captureId).toBe('after');expect(started.details.firstAudioMs).toBeUndefined();
   expect(onDebug.mock.calls.some(([e])=>e.name==='devicePlayback.audio.first')).toBe(false);
   await client.stop();
 });
 it('samples PCM statistics and separates consumed start from physical output and history ACK',async()=>{
   const onDebug=vi.fn();const {client,socket}=await connected({onDebug});await voice(client,socket);client.setDebugCapture('capture');
   const mic=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-mic-capture')!;
   for(let i=0;i<20;i++)mic.port.onmessage?.({data:{type:'frame',samples:new Int16Array(160)}} as MessageEvent);
   socket.bufferedAmount=999999;mic.port.onmessage?.({data:{type:'frame',samples:new Int16Array(160)}} as MessageEvent);
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'clocked',text:'hidden',mode:'voice',...cue}));
   socket.message(new Int16Array(2400).buffer);
   const playback=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')!;
   await vi.waitFor(()=>expect(playback.port.sent.some((e:any)=>e.type==='append')).toBe(true));
   playback.port.onmessage?.({data:{type:'clause-start',clauseId:'clocked',contextTime:0.1}} as MessageEvent);
   expect(onDebug.mock.calls.some(([e])=>e.name==='devicePlayback.consumed.started')).toBe(true);
   expect(onDebug.mock.calls.some(([e])=>e.name==='devicePlayback.physical.started')).toBe(false);
   FakeAudioContext.instances[0].outputTime=0.1;
   await vi.waitFor(()=>expect(onDebug.mock.calls.some(([e])=>e.name==='devicePlayback.physical.started')).toBe(true));
   expect(onDebug.mock.calls.filter(([e])=>e.name==='devicePlayback.audio.first')).toHaveLength(1);
   expect(onDebug.mock.calls.find(([e])=>e.name==='devicePlayback.physical.started')?.[0].details.firstAudioMs).toBeGreaterThanOrEqual(0);
   playback.port.onmessage?.({data:{type:'playback-progress',clauseId:'clocked',consumedSamples:2400}} as MessageEvent);
   playback.port.onmessage?.({data:{type:'clause-complete',clauseId:'clocked',endContextTime:0.1}} as MessageEvent);
   await vi.waitFor(()=>expect(onDebug.mock.calls.some(([e])=>e.name==='renderer.ack.sent')).toBe(true));
   const names=onDebug.mock.calls.map(([e])=>e.name);
   expect(names.indexOf('devicePlayback.physical.completed')).toBeLessThan(names.indexOf('history.write.started'));
   expect(names.indexOf('history.write.completed')).toBeLessThan(names.indexOf('renderer.ack.sent'));
   expect(names.filter(n=>n==='audio.stats')).toHaveLength(1);
   const events=onDebug.mock.calls.map(([e])=>e);expect(new Set(events.map(e=>e.seq)).size).toBe(events.length);await client.stop();
 });

});
