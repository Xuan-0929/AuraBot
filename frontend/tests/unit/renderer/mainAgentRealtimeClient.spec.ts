/**
 * Verifies the Pet's minimal voice-session contract at browser boundaries. The suite covers profile-bound
 * token acquisition, one shared AudioContext, raw microphone/TTS PCM, server/local playback gates, physical
 * cursor ACK, and one teardown path without relying on the removed diagnostic protocol or real hardware.
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

const profile={profileId:'profile_default',soul:'Aura'};
async function connected(options:ConstructorParameters<typeof MainAgentRealtimeClient>[0]={}){
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({token:'token'})})));
 vi.stubGlobal('navigator',{mediaDevices:{getUserMedia:vi.fn(async()=>({getTracks:()=>[]}))}});
 vi.stubGlobal('WebSocket',FakeWebSocket);vi.stubGlobal('AudioContext',FakeAudioContext);vi.stubGlobal('AudioWorkletNode',FakeAudioWorkletNode);
 const client=new MainAgentRealtimeClient(options);const connecting=client.start(profile);
 await vi.waitFor(()=>expect(FakeWebSocket.instances).toHaveLength(1));
 const socket=FakeWebSocket.instances[0];socket.open();socket.message(JSON.stringify({type:'session.ready'}));await connecting;
 return {client,socket};
}
async function voice(client:MainAgentRealtimeClient,socket:FakeWebSocket){
 const enabling=client.toggle(profile);
 await vi.waitFor(()=>expect(sentControls(socket).some(e=>e.type==='voice.start')).toBe(true));
 const command=sentControls(socket).find(e=>e.type==='voice.start')!;
 socket.message(JSON.stringify({type:'command.ack',command_id:command.command_id,status:'accepted'}));await enabling;
}
describe('formal Pet unified realtime client',()=>{
 afterEach(()=>{vi.unstubAllGlobals();FakeWebSocket.instances.length=0;FakeAudioWorkletNode.instances.length=0;FakeAudioContext.instances.length=0;});
 it('connects text without microphone and awaits durable delivery before ACK',async()=>{
   let finish!:()=>void;const stored=new Promise<void>(resolve=>finish=resolve);const persist=vi.fn(()=>stored);
   const {client,socket}=await connected({onMessage:persist});expect(FakeAudioContext.instances).toHaveLength(0);
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'text-1',text:'hello',mode:'text'}));
   await vi.waitFor(()=>expect(persist).toHaveBeenCalledWith({id:'text-1',role:'assistant',content:'hello'}));
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
   const {client,socket}=await connected({onMessage:async()=>{throw new Error('disk full');}});
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'full',text:'hello',mode:'text'}));
   await vi.waitFor(()=>expect(client.currentState).toBe('error'));expect(sentControls(socket).filter(e=>e.type==='renderer.ack'&&e.complete)).toHaveLength(0);
 });
 it('keeps text available when microphone permission is denied',async()=>{
   const {client,socket}=await connected();vi.stubGlobal('navigator',{mediaDevices:{getUserMedia:async()=>{throw new Error('permission denied');}}});
   await expect(client.toggle(profile)).rejects.toThrow('permission denied');expect(socket.readyState).toBe(1);expect(client.voiceEnabled).toBe(false);await client.stop();
 });
 it('orders PCM after its offer while accepted-user history is pending',async()=>{
   let finish!:()=>void;const write=new Promise<void>(resolve=>finish=resolve);
   const {client,socket}=await connected({onMessage:()=>write});await voice(client,socket);
   socket.message(JSON.stringify({type:'user.message',message_id:'u',text:'hello'}));
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'v',text:'hello',mode:'voice'}));
   socket.message(new Int16Array([1,2]).buffer);finish();
   const playback=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')!;
   await vi.waitFor(()=>expect(playback.port.sent).toContainEqual({type:'append',samples:new Int16Array([1,2])}));
   expect(client.currentState).toBe('active');await client.stop();
 });
 it('does not ACK an old persisted clause onto a replacement connection',async()=>{
   let finish!:()=>void;const write=new Promise<void>(resolve=>finish=resolve);const persist=vi.fn(()=>write);
   const {client,socket}=await connected({onMessage:persist});
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'old',text:'hello',mode:'text'}));
   await vi.waitFor(()=>expect(persist).toHaveBeenCalled());await client.stop();
   const connecting=client.start(profile);await vi.waitFor(()=>expect(FakeWebSocket.instances).toHaveLength(2));
   const next=FakeWebSocket.instances[1];next.open();next.message(JSON.stringify({type:'session.ready'}));await connecting;
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
   const {client,socket}=await connected({onMessage:persist});await voice(client,socket);
   socket.message(JSON.stringify({type:'clause.offer',clause_id:'late',text:'heard',mode:'voice'}));
   const playback=FakeAudioWorkletNode.instances.find(n=>n.processorName==='aurabot-tts-playback')!;
   await vi.waitFor(()=>expect(playback.port.sent).toContainEqual({type:'offer',clauseId:'late'}));
   playback.port.onmessage?.({data:{type:'clause-complete',clauseId:'late',endContextTime:0}} as MessageEvent);
   await vi.waitFor(()=>expect(persist).toHaveBeenCalledOnce());
   socket.message(JSON.stringify({type:'clause.cancel',clause_id:'late'}));
   await new Promise(resolve=>setTimeout(resolve,0));finish();await new Promise(resolve=>setTimeout(resolve,0));
   expect(sentControls(socket).filter(e=>e.type==='renderer.ack')).toEqual([]);expect(client.currentState).toBe('active');await client.stop();
 });

 it('does not let a slow old AudioContext shutdown overwrite a replacement session',async()=>{
   const {client,socket}=await connected();await voice(client,socket);
   let finish!:()=>void;FakeAudioContext.instances[0].close.mockImplementation(()=>new Promise<void>(resolve=>finish=resolve));
   const stopping=client.stop();const connecting=client.start(profile);
   await vi.waitFor(()=>expect(FakeWebSocket.instances).toHaveLength(2));
   const next=FakeWebSocket.instances[1];next.open();next.message(JSON.stringify({type:'session.ready'}));await connecting;
   finish();await stopping;expect(client.currentState).toBe('active');await client.stop();
 });

});
