<!-- Uses shared Pet minimum dimensions so renderer layout follows the native resize limits. -->
<!-- Connects realtime voice/state delivery to the resident Live2D scene and desktop controls.
     Backend no_audio maps to Profile idle; only Backend VAD selects listening. Scene actions
     and state requests share the opt-in Live Debug log to trace acoustic-to-animation handoffs.
     Voice ownership is reported through Main so Realtime can disable text input across windows. -->
<!-- Gates token and microphone access on the active Profile snapshot; Main reconnect commands complete activation. -->
<!--
  桌宠窗口：组合 Live2D 舞台、唯一的 Main Agent 文字／语音连接与窗口控制，不显示对话文字气泡。
  语音 clause 经 AudioWorklet 确认播放完成后，由 Electron 持久化到对话记录并 ACK；文字无需麦克风。
  控件根据已有全局光标事件显隐，以真实麦克风状态提供持续标记；透明窗口命中与捕获由 useClickThrough 协调。
  可选调试采集订阅 Main 状态并将客户端标量事件转发 IPC；订阅先于初始读取以防竞态，卸载时释放。
  Task 业务归 Backend；Live2D 表演由实例播放器消费实际交付事件，窗口交互委托给 preload IPC。
-->
<template>
  <main
    class="desktop-pet pet-vue-root"
    :style="{ minWidth: PET_WINDOW_MIN_SIZE.width + 'px', minHeight: PET_WINDOW_MIN_SIZE.height + 'px' }"
    :data-agent-status="petStore.agentStatus"
    :data-click-through="petStore.petSettings.clickThroughPet ? 'true' : 'false'"
    :data-resizing="resizeDraggingDirection ? 'true' : 'false'"
    :data-controls-visible="isHoverUiVisible ? 'true' : 'false'"
    :aria-label="t(petStore.displayLanguage, 'desktopLive2DPet')"
    @pointerenter="isPointerInside = true"
    @pointermove="handlePointerMove"
    @pointerleave="handlePointerLeave"
    @wheel="handleWheel"
  >
    <p v-if="visualError" class="pet-voice-error" role="status">{{ t(petStore.displayLanguage, 'visualUnavailable') }}：{{ visualError }}</p>
    <p v-if="voiceError" class="pet-voice-error" role="alert">{{ voiceError }}</p>
    <div class="desktop-pet-model-shell pet-vue-model-shell" data-testid="live2d-model-surface">
      <div class="pet-vue-live2d-frame">
        <Scene
          ref="scene"
          :adaptor="petStore.live2dModelState?.adaptor"
          :profile-id="petStore.live2dModelState?.profileId"
          :revision="petStore.live2dModelState?.revision"
          :model-version="petStore.live2dModelState?.modelVersion"
          :generation="transportGeneration"
          @error="message => visualError = message"
          :pet-ready="petStore.isInitialized"
          :model-path="petStore.modelPath"
          :focus-at="petStore.focusAt"
          :pointer-at="pointerAt"
          :view-transform="live2dViewTransform"
          :zoom-request="live2dZoomRequest"
          @canvas-ready="handleCanvasReady"
          @model-ready="syncAgentState"
          @playback-change="name => reportAvatarDebug('avatar.resident', { text: name })"
          @view-transform-change="handleLive2DViewTransformChange"
        />
      </div>
      <ControlButtons
        :visible="isHoverUiVisible"
        :display-language="petStore.displayLanguage"
        :voice-input-active="isVoiceInputActive"
        :voice-input-state="microphoneState"
        :voice-unavailable="!profileReady"
        :voice-unavailable-reason="petStore.profileRuntime?.error || t(petStore.displayLanguage,'profilePreparing')"
        @hovering-change="(value) => (isControlsHovering = value)"
        @focus-change="(value) => (isControlsFocused = value)"
        @dragging-change="(value) => (isControlDragging = value)"
        @console-open="() => void petStore.openConsoleWindow()"
        @voice-toggle="handleVoiceToggle"
      />
    </div>

    <div
      class="pet-vue-resize-grips"
      aria-hidden="true"
      @pointerenter="isResizeGripHovering = true"
      @pointerleave="onResizeGripsPointerLeave"
    >
      <div
        v-for="direction in resizeDirections"
        :key="direction"
        :class="['pet-vue-resize-grip', `pet-vue-resize-grip--${direction}`]"
        @pointerdown="(e) => handleResizePointerDown(direction, e)"
        @pointermove="(e) => handleResizePointerMove(direction, e)"
        @pointerup="handleResizePointerEnd"
        @pointercancel="handleResizePointerEnd"
        @lostpointercapture="handleResizePointerEnd"
      />
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { PET_WINDOW_MIN_SIZE } from "@shared/contracts/petWindow";
import type { PetResizeDirection } from "@shared/contracts/ipc";
import type { DebugScalar } from "@shared/contracts/liveDebug";
import {
  MainAgentRealtimeClient,
  type MainAgentRealtimeState
} from "../../main-agent-realtime/client";
import { useAuraBotStore } from "../../app/store";
import { t } from "../../i18n";
import {
  readStoredLive2DViewTransform,
  writeStoredLive2DViewTransform
} from "../live2d/live2dViewScale";
import type { Live2DViewTransform } from "../live2d/live2dDiagnostics";
import { useClickThrough } from "./composables/useClickThrough";
import ControlButtons from "./components/ControlButtons.vue";
import Scene from "./live2d/Scene.vue";

const resizeDirections: PetResizeDirection[] = ["n", "s", "e", "w", "nw", "ne", "sw", "se"];

const petStore = useAuraBotStore();
const scene=ref<InstanceType<typeof Scene>>();
const visualError=ref('');
const transportGeneration=ref(0);

const isControlsHovering = ref(false);
const isPointerInside = ref(false);
const isControlsFocused = ref(false);
const isControlDragging = ref(false);
const isVoiceConnecting = ref(false);
const profileReady=computed(()=>Boolean(petStore.profileRuntime?.online&&!petStore.profileRuntime.activating&&petStore.profileRuntime.activeId===petStore.profile?.id&&petStore.profileRuntime.revision));
const voiceEnabled=ref(false);
const voiceError=ref("");
const microphoneError = ref(false);
const isVoiceInputActive = computed(
  () => voiceEnabled.value
);
const microphoneState = computed<MainAgentRealtimeState>(() =>
  isVoiceConnecting.value ? "connecting" : microphoneError.value ? "error" : voiceEnabled.value ? "active" : "off"
);
const isResizeGripHovering = ref(false);
const resizeDraggingDirection = ref<PetResizeDirection | null>(null);
const isHoverUiVisible = computed(() => isPointerInside.value || isControlsFocused.value || isControlDragging.value || resizeDraggingDirection.value !== null);
const pointerAt = ref<{ x: number; y: number } | undefined>(undefined);
const canvasRef = ref<HTMLCanvasElement | null>(null);
const live2dViewTransform = ref(readStoredLive2DViewTransform(window.localStorage));
const live2dZoomRequest = ref<{ id: number; deltaY: number; focalPoint: { x: number; y: number } } | undefined>(undefined);

const mainAgentRealtime = new MainAgentRealtimeClient({
  displayLanguage:()=>petStore.displayLanguage,
  getToken:async()=>{const token=await window.aurabot.getRealtimeToken();transportGeneration.value=token.generation;await nextTick();return token;},
  onDebug:event=>{void window.aurabot.reportLiveDebugEvent(event).catch(()=>undefined);},
  onPerformance:event=>scene.value?.accept(event),
  onState: (state,detail) => {
    if (state === "error" && (voiceEnabled.value || isVoiceConnecting.value)) microphoneError.value = true;
    if(state!=="active")voiceEnabled.value=false;
    voiceError.value=detail??"";
    void window.aurabot.reportRealtimeError(detail??"");
  },
  onMessage: async(entry)=>{await window.aurabot.recordDelivery(entry);},
  onAgentState:state=>{void window.aurabot.reportRealtimeState(state);},
  onVoiceState:active=>{void window.aurabot.reportRealtimeState(petStore.agentStatus,active);}
});

let debugDisposed=false;
let debugVersion=0;
let avatarDebugCaptureId:string|null=null;
let avatarDebugSeq=0;
const avatarDebugInstanceId=crypto.randomUUID();
function reportAvatarDebug(name:string,details:Record<string,DebugScalar>){
  if(debugDisposed||!avatarDebugCaptureId)return;
  const diagnostics=scene.value?.diagnostics();
  void window.aurabot.reportLiveDebugEvent({source:'pet',instanceId:avatarDebugInstanceId,
    seq:++avatarDebugSeq,captureId:avatarDebugCaptureId,timestamp:Date.now(),stage:'playback',name,level:'info',
    profileId:petStore.profile?.id,generation:transportGeneration.value,
    details:{state:petStore.agentStatus,elapsedMs:diagnostics?.elapsedMs??0,count:diagnostics?.residentStarts??0,...details}
  }).catch(()=>undefined);
}
const unsubscribeDebug=window.aurabot.onLiveDebugCaptureChanged(value=>{
  debugVersion++;
  if(!debugDisposed){avatarDebugCaptureId=value.enabled?value.captureId:null;mainAgentRealtime.setDebugCapture(avatarDebugCaptureId);}
});
const initialDebugVersion=debugVersion;
void window.aurabot.getLiveDebugCapture().then(value=>{
  if(!debugDisposed&&debugVersion===initialDebugVersion){avatarDebugCaptureId=value.enabled?value.captureId:null;mainAgentRealtime.setDebugCapture(avatarDebugCaptureId);}
}).catch(()=>undefined);

let unsubscribeCommand:(()=>void)|undefined;
async function connect(){
  await petStore.loadInitialState();
  const profile=petStore.profile;if(!profile||!profileReady.value)throw new Error(petStore.profileRuntime?.error||t(petStore.displayLanguage,"profilePreparing"));
  await nextTick();
  const visual=await scene.value?.ready()??{available:false,error:'未配置模型'};visualError.value=visual.error??'';
  await mainAgentRealtime.start({profileId:profile.id,revision:petStore.profileRuntime?.revision});
  return visual;
}
onMounted(async()=>{
  unsubscribeCommand=window.aurabot.onPetCommand(async command=>{
    try{
      let visual:{available:boolean;error?:string}|undefined;
      if(command.type==="disconnect")await mainAgentRealtime.stop();
      else if(command.type==="reconnect"){await mainAgentRealtime.stop();visual=await connect();}
      else {await connect();await mainAgentRealtime.submitText(command.text??"",command.id);}
      await window.aurabot.petCommandResult({id:command.id,visual});
    }catch(error){const detail=error instanceof Error?error.message:t(petStore.displayLanguage, 'connectionFailed');voiceError.value=detail;await window.aurabot.petCommandResult({id:command.id,error:detail});}
  });
  await window.aurabot.petReady();
  const settings=await window.aurabot.getSettings();
  await petStore.loadInitialState();
  if(settings.online&&profileReady.value)try{await connect();}catch(error){voiceError.value=String(error);}
});
onBeforeUnmount(()=>{debugDisposed=true;unsubscribeDebug();mainAgentRealtime.setDebugCapture(null);unsubscribeCommand?.();void mainAgentRealtime.stop();});

const clickThrough = useClickThrough({
  enabled: computed(() => petStore.petSettings.clickThroughPet),
  canvasRef,
  interactiveUiHovering: computed(
    () => isControlsHovering.value || isControlsFocused.value || isControlDragging.value || isResizeGripHovering.value || resizeDraggingDirection.value !== null
  )
});

// The existing cursor IPC also reports coordinates outside the transparent window,
// where native pointerleave is not reliable while Electron forwards mouse moves.
watch(() => petStore.petCursorPosition, async (position) => {
  if (!position) return;
  const { localX: x, localY: y } = position;
  isPointerInside.value = x >= 0 && y >= 0 && x < window.innerWidth && y < window.innerHeight;
  await nextTick();
  const target = isPointerInside.value ? document.elementFromPoint?.(x, y) : null;
  isControlsHovering.value = Boolean(target?.closest(".pet-action-button"));
  isResizeGripHovering.value = Boolean(target?.closest(".pet-vue-resize-grip"));
  if (isPointerInside.value) void clickThrough.handlePointerMove();
  else void clickThrough.handlePointerLeave();
});

watch(
  () => petStore.petActivity?.id,
  (activityId) => {
    if (!activityId) {
      return;
    }
    petStore.armPetActivityAutoClear(activityId);
  },
  { immediate: true }
);

function syncAgentState(){
  const state=petStore.agentStatus;const target=state==='typing'?'thinking':state==='no_audio'?'idle':state;
  reportAvatarDebug('avatar.state',{phase:target});
  void scene.value?.setState(target);
}
watch(()=>petStore.agentStatus,syncAgentState);
watch(()=>petStore.profile?.id,id=>{live2dViewTransform.value=readStoredLive2DViewTransform(window.localStorage,id);});

async function handleVoiceToggle() {
  const profile=petStore.profile;if(!profile)return;
  if (isVoiceConnecting.value) return;
  if(!profileReady.value){voiceError.value=petStore.profileRuntime?.error||t(petStore.displayLanguage,"profilePreparing");return;}
  voiceError.value="";
  microphoneError.value = false;
  isVoiceConnecting.value = true;
  try {await mainAgentRealtime.toggle({profileId:profile.id,revision:petStore.profileRuntime?.revision});voiceEnabled.value=mainAgentRealtime.voiceEnabled;}
  catch(error){voiceEnabled.value=false;microphoneError.value=true;voiceError.value=error instanceof Error?error.message:t(petStore.displayLanguage, 'voiceUnavailable');}
  finally { isVoiceConnecting.value = false; }
}

function handleCanvasReady(canvas: HTMLCanvasElement) {
  canvasRef.value = canvas;
}

function onResizeGripsPointerLeave() {
  if (resizeDraggingDirection.value !== null) {
    return;
  }
  isResizeGripHovering.value = false;
}

function handleResizePointerDown(direction: PetResizeDirection, event: PointerEvent) {
  event.preventDefault();
  void clickThrough.setPassthrough(false);
  const target = event.currentTarget as HTMLElement | null;
  if (!target) {
    return;
  }
  target.setPointerCapture(event.pointerId);
  resizeDraggingDirection.value = direction;
}

function handleResizePointerMove(direction: PetResizeDirection, event: PointerEvent) {
  if (resizeDraggingDirection.value !== direction) {
    return;
  }
  void window.aurabot.resizePetWindowCorner(direction, {
    x: event.movementX,
    y: event.movementY
  });
}

function handleResizePointerEnd(event: PointerEvent) {
  const target = event.currentTarget as HTMLElement | null;
  if (target?.hasPointerCapture(event.pointerId)) {
    target.releasePointerCapture(event.pointerId);
  }
  resizeDraggingDirection.value = null;
}

function handlePointerLeave() {
  isPointerInside.value = false;
  isControlsHovering.value = false;
  isResizeGripHovering.value = false;
  void clickThrough.handlePointerLeave();
}

function handlePointerMove(event: PointerEvent) {
  isPointerInside.value = true;
  const canvas = canvasRef.value;
  if (canvas) {
    const rect = canvas.getBoundingClientRect();
    pointerAt.value = { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }
  void clickThrough.handlePointerMove();
}

function handleWheel(event: WheelEvent) {
  if (!event.ctrlKey && !event.metaKey) {
    return;
  }

  event.preventDefault();
  const canvas = canvasRef.value;
  if (!canvas) {
    return;
  }

  const rect = canvas.getBoundingClientRect();
  live2dZoomRequest.value = {
    id: Date.now(),
    deltaY: event.deltaY,
    focalPoint: {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top
    }
  };
}

function handleLive2DViewTransformChange(transform: Live2DViewTransform) {
  live2dViewTransform.value = transform;
  writeStoredLive2DViewTransform(window.localStorage, transform, petStore.profile?.id);
}
</script>

<style>
/* The shared body minimum is for other windows; Pet owns its smaller minimum. */
body:has(.pet-vue-root) { min-width: 0; }
.pet-vue-root {
  /* Fill the pet window so the model shell (and anchored controls) track the resizable frame. */
  place-items: stretch;
}

/* Large transparent corners — programmatic window resize via IPC (see `resizePetWindowCorner`).
 * Must stack above `.pet-quick-controls` / `.pet-hover-chat` (z-index: 4 in styles.css). */
.pet-vue-resize-grips {
  position: absolute;
  inset: 0;
  z-index: 12;
  pointer-events: none;
}

.pet-vue-resize-grip {
  position: absolute;
  box-sizing: border-box;
  pointer-events: auto;
  touch-action: none;
  background: transparent;
}

.pet-vue-resize-grip--n,
.pet-vue-resize-grip--s {
  left: 32px;
  right: 32px;
  height: 14px;
}

.pet-vue-resize-grip--e,
.pet-vue-resize-grip--w {
  top: 32px;
  bottom: 32px;
  width: 14px;
}

.pet-vue-resize-grip--nw,
.pet-vue-resize-grip--ne,
.pet-vue-resize-grip--sw,
.pet-vue-resize-grip--se {
  width: 32px;
  height: 32px;
}

.pet-vue-resize-grip--n {
  top: 0;
  cursor: ns-resize;
}

.pet-vue-resize-grip--s {
  bottom: 0;
  cursor: ns-resize;
}

.pet-vue-resize-grip--e {
  right: 0;
  cursor: ew-resize;
}

.pet-vue-resize-grip--w {
  left: 0;
  cursor: ew-resize;
}

.pet-vue-resize-grip--nw {
  top: 0;
  left: 0;
  cursor: nwse-resize;
}

.pet-vue-resize-grip--ne {
  top: 0;
  right: 0;
  cursor: nesw-resize;
}

.pet-vue-resize-grip--sw {
  bottom: 0;
  left: 0;
  cursor: nesw-resize;
}

.pet-vue-resize-grip--se {
  right: 0;
  bottom: 0;
  cursor: nwse-resize;
}

.pet-vue-model-shell {
  border-radius: 8px;
  box-shadow: inset 0 0 0 1px transparent;
}

.pet-vue-live2d-frame {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
}

.pet-vue-root[data-resizing="true"] .pet-vue-model-shell {
  box-shadow: inset 0 0 0 1px var(--aura-accent);
}

.pet-vue-resize-grips::after {
  content: "";
  position: absolute;
  right: 5px;
  bottom: 5px;
  width: 10px;
  height: 10px;
  border-right: 1px solid var(--aura-accent);
  border-bottom: 1px solid var(--aura-accent);
  opacity: 0;
  transition: opacity 140ms ease;
}
.pet-vue-root[data-controls-visible="true"] .pet-vue-resize-grips::after,
.pet-vue-root:focus-within .pet-vue-resize-grips::after,
.pet-vue-root[data-resizing="true"] .pet-vue-resize-grips::after { opacity: 1; }
@media (prefers-reduced-motion: reduce) { .pet-vue-resize-grips::after { transition: none; } }

.pet-vue-model-shell .desktop-pet-stage,
.pet-vue-model-shell .live2d-canvas-host,
.pet-vue-model-shell .live2d-canvas {
  pointer-events: none;
}

</style>
