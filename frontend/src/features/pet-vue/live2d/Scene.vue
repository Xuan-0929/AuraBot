<!-- Composes the canvas and its resident avatar player, forwarding explicit preview/performance
     commands without shared mutable state. Layout and hit-testing remain local to this scene. -->
<template>
  <section class="desktop-pet-stage pet-vue-desktop-pet-stage">
    <div class="desktop-pet-stage-main">
      <Canvas @ready="handleCanvasReady" @resized="handleCanvasResized" />
      <Model
        v-if="petReady"
        ref="modelRef"
        :adaptor="adaptor" :profile-id="profileId" :revision="revision" :model-version="modelVersion" :generation="generation"
        :app="app"
        :model-path="modelPath"
        :focus-at="focusAt"
        :canvas-size="canvasSize"
        :pointer-at="pointerAt"
        :view-transform="viewTransform"
        :zoom-request="zoomRequest"
        @interactive-change="(value) => emit('interactiveAreaChange', value)"
        @playback-change="name=>emit('playbackChange',name)"
        @model-ready="() => emit('modelReady')"
        @error="message => emit('error', message)"
        @view-transform-change="(value) => emit('viewTransformChange', value)"
      />
    </div>
    <slot />
  </section>
</template>

<script setup lang="ts">
import { ref, shallowRef } from "vue";
import type { Application } from "pixi.js";
import type { Live2DViewTransform } from "../../live2d/live2dDiagnostics";
import Canvas from "./Canvas.vue";
import Model from "./Model.vue";

import type { AvatarAdaptor,AvatarState } from "@shared/contracts/avatar";
import type { PerformanceEvent } from "../../live2d/performance";
defineProps<{
  adaptor?:AvatarAdaptor; profileId?:string; revision?:number; modelVersion?:string; generation?:number;
  petReady: boolean;
  modelPath: string;
  focusAt: { x: number; y: number };
  pointerAt?: { x: number; y: number };
  viewTransform: Live2DViewTransform;
  zoomRequest?: { id: number; deltaY: number; focalPoint: { x: number; y: number } };
}>();

const emit = defineEmits<{
  playbackChange:[string];
  canvasReady: [HTMLCanvasElement];
  interactiveAreaChange: [boolean];
  modelReady: [];
  error:[string];
  viewTransformChange: [Live2DViewTransform];
}>();

const app = shallowRef<Application | null>(null);
const modelRef=ref<InstanceType<typeof Model>>();
defineExpose({ready:()=>modelRef.value?.ready()??Promise.resolve({available:false,error:'未配置模型'}),play:(input:{motion?:string;expression?:string;loop?:boolean})=>modelRef.value?.play(input),stop:()=>modelRef.value?.stop(),setState:(state:AvatarState)=>modelRef.value?.setState(state),accept:(event:PerformanceEvent)=>modelRef.value?.accept(event),validate:(file:string,kind:'motion'|'expression')=>{if(!modelRef.value)throw new Error('模型尚未加载');return modelRef.value.validate(file,kind);},diagnostics:()=>modelRef.value?.diagnostics()});
const canvasSize = ref({ width: 1, height: 1 });

function handleCanvasReady(payload: { app: Application; canvas: HTMLCanvasElement }) {
  app.value = payload.app;
  emit("canvasReady", payload.canvas);
}

function handleCanvasResized(payload: { width: number; height: number }) {
  canvasSize.value = payload;
}
</script>
