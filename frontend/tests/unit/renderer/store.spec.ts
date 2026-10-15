/**
 * 同时验证显示清除快照和历史分页竞态，旧请求不能覆盖清除后的显示。
 * 验证 Renderer store 的普通聊天、附件与非 Task 事件订阅。
 *
 * 测试不构造 Task DTO，确认 optimistic user message 会被服务端消息替换且 preload
 * subscriptions 只包含保留的聊天/桌宠事件。并发快照刷新必须等待最新角色状态落地，
 * 避免配置应用后的重连读取仍在激活中的旧状态。
 */

import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuraBotStore } from "../../../src/app/store";
import type { SendChatMessageResult } from "@shared/contracts/chat";
import type { Message } from "@shared/schemas/message";

function message(id: string, role: Message["role"], content: string): Message {
  return {
    id,
    sessionId: "session-1",
    profileId: "profile-1",
    role,
    content,
    createdAt: "2026-08-19T00:00:00.000Z"
  };
}

describe("useAuraBotStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.unstubAllGlobals();
  });

  it("exposes Profile and model registry loaded through a Pinia patch",async()=>{
    const profile={id:"profile-test",displayName:"Aura",active:true,createdAt:"now",updatedAt:"now"};
    const model={currentModelId:"hiyori",currentModelPath:"assets/model.json",models:[]};
    vi.stubGlobal("window",{aurabot:{getInitialState:async()=>({profile,live2dModelState:model})}});
    const store=useAuraBotStore();await store.loadInitialState();
    expect(store.profile).toEqual(profile);expect(store.live2dModelState).toEqual(model);
  });

  it("reconciles an optimistic chat message without Task state", async () => {
    const result: SendChatMessageResult = {
      userMessage: message("user-server", "user", "你好"),
      agentStatus: "idle"
    };
    vi.stubGlobal("window", {
      aurabot: { sendChatMessage: vi.fn(async () => result) }
    });
    const store = useAuraBotStore();
    store.profile = {
      id: "profile-1",
      displayName: "User",
      active: true,
      createdAt: "now",
      updatedAt: "now"
    };
    store.mainSessionId = "session-1";

    await store.sendMessage("  你好  ");

    expect(store.messages.map((entry) => entry.id)).toEqual(["user-server"]);
    expect(store.agentStatus).toBe("idle");
    expect(store).not.toHaveProperty("tasks");
  });

  it("subscribes to retained pet/chat events and updates cursor focus", () => {
    let cursorListener:
      | ((value: { screenX: number; screenY: number; localX: number; localY: number }) => void)
      | undefined;
    const unsubscribe = vi.fn();
    vi.stubGlobal("window", {
      aurabot: {
        onRealtimeError:vi.fn(()=>unsubscribe),
        onPetSettingsChanged: vi.fn(() => unsubscribe),
        onPetActivity: vi.fn(() => unsubscribe),
        onLive2DModelStateChanged: vi.fn(() => unsubscribe),
        onPetCursorPosition: vi.fn((listener) => {
          cursorListener = listener;
          return unsubscribe;
        }),
        onAgentStatusChanged: vi.fn(() => unsubscribe),
        onChatMessageUpdated: vi.fn(() => unsubscribe)
      }
    });
    const store = useAuraBotStore();

    const stop = store.subscribeToPetEvents();
    cursorListener?.({ screenX: 10, screenY: 20, localX: 3, localY: 4 });

    expect(store.focusAt).toEqual({ x: 3, y: 4 });
    stop();
    expect(unsubscribe).toHaveBeenCalledTimes(7);
  });

  it('loads older visible history without duplicating existing messages',async()=>{
    const message={id:'old',sessionId:'main:p',profileId:'p',role:'user' as const,content:'hello',createdAt:'2026-09-05T00:00:00Z'};
    vi.stubGlobal('window',{aurabot:{getHistory:vi.fn(async()=>({messages:[message]}))}});
    const store=useAuraBotStore();store.historyCursor=10;await store.loadOlderHistory();
    expect(store.messages).toEqual([message]);expect(store.historyCursor).toBeUndefined();
  });
});

it('discards late initial-state responses after a newer Profile snapshot has arrived',async()=>{
 setActivePinia(createPinia());let first!:(value:unknown)=>void;
 const a={profile:{id:'a'},messages:[],profileRuntime:{generation:1}};
 const b={profile:{id:'b'},messages:[message('b','user','B')],profileRuntime:{generation:2}};
 let calls=0;vi.stubGlobal('window',{aurabot:{getInitialState:()=>++calls===1?new Promise(resolve=>first=resolve):Promise.resolve(b)}});
 const store=useAuraBotStore();const stale=store.loadInitialState();await store.loadInitialState();first(a);await stale;
 expect(store.profile?.id).toBe('b');expect(store.profileRuntime?.generation).toBe(2);
});
it('keeps reconnect waiting when a Profile event supersedes its in-flight snapshot',async()=>{
 setActivePinia(createPinia());
 const replies:Array<(value:unknown)=>void>=[];
 vi.stubGlobal('window',{aurabot:{getInitialState:()=>new Promise(resolve=>replies.push(resolve))}});
 const store=useAuraBotStore();
 store.profileRuntime={selectedId:'a',activeId:null,revision:1,generation:1,online:false,activating:true};
 let connected=false;
 const reconnect=store.loadInitialState().then(()=>{connected=true;});
 const refresh=store.loadInitialState();
 replies[0]!({profile:{id:'a'},profileRuntime:{selectedId:'a',activeId:'a',revision:1,generation:2,online:true,activating:false}});
 await new Promise(resolve=>setTimeout(resolve,0));
 const completedBeforeLatest=connected;
 replies[1]!({profile:{id:'a'},profileRuntime:{selectedId:'a',activeId:'a',revision:1,generation:2,online:true,activating:false}});
 await Promise.all([reconnect,refresh]);
 expect(completedBeforeLatest).toBe(false);
 expect(store.profileRuntime?.online).toBe(true);
 expect(store.profileRuntime?.activating).toBe(false);
});
it('initializes from the filtered snapshot without fetching unfiltered history',async()=>{
 setActivePinia(createPinia());const getHistory=vi.fn();
 vi.stubGlobal('window',{aurabot:{getInitialState:async()=>({profile:{id:'a'},messages:[],historyCursor:65}),getHistory}});
 const store=useAuraBotStore();await store.initialize();
 expect(store.messages).toEqual([]);expect(store.historyCursor).toBe(65);expect(getHistory).not.toHaveBeenCalled();
});
it('discards older history fetched before a clear or profile refresh and prevents overlapping pagination',async()=>{
 setActivePinia(createPinia());let resolve!:(value:unknown)=>void;
 const getHistory=vi.fn(()=>new Promise(done=>resolve=done));
 vi.stubGlobal('window',{aurabot:{getHistory,getInitialState:async()=>({profile:{id:'a'},messages:[],historyCursor:90})}});
 const store=useAuraBotStore();await store.loadInitialState();
 const loading=store.loadOlderHistory();await store.loadOlderHistory();expect(getHistory).toHaveBeenCalledOnce();
 await store.loadInitialState();resolve({messages:[message('old','user','old')],nextCursor:10});await loading;
 expect(store.messages).toEqual([]);expect(store.historyCursor).toBe(90);expect(store.historyLoading).toBe(false);
});

it('updates history rows and pagination cursor together on Profile and generation snapshots',async()=>{
 setActivePinia(createPinia());let cursor:number|undefined=80;const profile={id:'a'};
 vi.stubGlobal('window',{aurabot:{getInitialState:async()=>({profile,messages:[message('latest','user','visible')],historyCursor:cursor})}});
 const store=useAuraBotStore();store.historyCursor=10;await store.loadInitialState();expect(store.historyCursor).toBe(80);
 cursor=undefined;await store.loadInitialState();expect(store.historyCursor).toBeUndefined();
});

it('refreshes filtered history on display-cleared events from another window',async()=>{
 setActivePinia(createPinia());let cleared!:(profileId:string)=>void;
 const subscribe=()=>()=>{};
 const getInitialState=vi.fn(async()=>({profile:{id:'a'},messages:[],historyCursor:80}));
 vi.stubGlobal('window',{aurabot:{getInitialState,onHistoryDisplayCleared:(listener:typeof cleared)=>{cleared=listener;return subscribe();},onRealtimeError:subscribe,onPetSettingsChanged:subscribe,onPetActivity:subscribe,onLive2DModelStateChanged:subscribe,onPetCursorPosition:subscribe,onAgentStatusChanged:subscribe,onChatMessageUpdated:subscribe}});
 const store=useAuraBotStore();await store.loadInitialState();store.messages=[message('old','user','old')];
 const stop=store.subscribeToPetEvents();cleared('other');expect(getInitialState).toHaveBeenCalledOnce();
 cleared('a');await vi.waitFor(()=>expect(store.messages).toEqual([]));expect(store.historyCursor).toBe(80);stop();
});
it('does not resurrect a cleared message when its pending send acknowledgement arrives',async()=>{
 setActivePinia(createPinia());let acknowledge!:(value:SendChatMessageResult)=>void;
 vi.stubGlobal('window',{aurabot:{getInitialState:async()=>({profile:{id:'profile-1'},messages:[],historyCursor:80}),sendChatMessage:()=>new Promise(resolve=>acknowledge=resolve)}});
 const store=useAuraBotStore();await store.loadInitialState();const sending=store.sendMessage('hello');
 await store.loadInitialState();acknowledge({userMessage:message('accepted','user','hello'),agentStatus:'idle'});await sending;
 expect(store.messages).toEqual([]);
});
