<!--
  桌宠窗口：组合 Live2D 舞台、唯一的 Main Agent 文字／语音连接、已交付消息气泡与窗口控制。
  语音 clause 经 AudioWorklet 确认播放完成后，由 Electron 持久化再显示并 ACK；文字无需麦克风。
  控件根据已有全局光标事件显隐，以真实麦克风状态提供持续标记；透明窗口命中与捕获由 useClickThrough 协调。
  Task 业务归 Backend；Live2D 表演由实例播放器消费实际交付事件，窗口交互委托给 preload IPC。
-->
<template>
  <main
    class="desktop-pet pet-vue-root"
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
    <aside v-if="mainAgentClauseText || petStore.petActivity" class="pet-message-bubble" :aria-label="t(petStore.displayLanguage, 'petMessage')">
      {{ mainAgentClauseText ?? petStore.petActivity?.message }}
    </aside>

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
          @view-transform-change="handleLive2DViewTransformChange"
        />
      </div>
      <ControlButtons
        :visible="isHoverUiVisible"
        :display-language="petStore.displayLanguage"
        :voice-input-active="isVoiceInputActive"
        :voice-input-state="microphoneState"
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
import type { PetResizeDirection } from "@shared/contracts/ipc";
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
const mainAgentClauseText = ref<string | null>(null);
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
  getToken:async()=>{const token=await window.aurabot.getRealtimeToken();transportGeneration.value=token.generation;await nextTick();return token;},
  onPerformance:event=>scene.value?.accept(event),
  onState: (state,detail) => {
    if (state === "error" && (voiceEnabled.value || isVoiceConnecting.value)) microphoneError.value = true;
    if(state!=="active")voiceEnabled.value=false;
    voiceError.value=detail??"";
    void window.aurabot.reportRealtimeError(detail??"");
  },
  onMessage: async(entry)=>{await window.aurabot.recordDelivery(entry);},
  onAgentState:state=>{void window.aurabot.reportRealtimeState(state);},
  onClause: (text) => {
    mainAgentClauseText.value = text;
    if(text)setTimeout(()=>{if(mainAgentClauseText.value===text)mainAgentClauseText.value=null;},7000);
  }
});

let unsubscribeCommand:(()=>void)|undefined;
async function connect(){
  await petStore.loadInitialState();
  const profile=petStore.profile;if(!profile)throw new Error("Profile 尚未就绪");
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
  if(settings.online)try{await connect();}catch(error){voiceError.value=String(error);}
});
onBeforeUnmount(()=>{unsubscribeCommand?.();void mainAgentRealtime.stop();});

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

function syncAgentState(){const state=petStore.agentStatus;void scene.value?.setState(state==='typing'?'thinking':state);}
watch(()=>petStore.agentStatus,syncAgentState);
watch(()=>petStore.profile?.id,id=>{live2dViewTransform.value=readStoredLive2DViewTransform(window.localStorage,id);mainAgentClauseText.value=null;});

async function handleVoiceToggle() {
  const profile=petStore.profile;if(!profile)return;
  if (isVoiceConnecting.value) return;
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
.pet-vue-root {
  min-width: 270px;
  min-height: 380px;
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
