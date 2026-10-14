<!-- Owns an isolated preview scene using the exact production player.
     Local pointer coordinates drive gaze for immediate sensitivity feedback. Validations acknowledge only
     resources actually parsed against this model; no conversation, audio or Memory connection exists. -->
<template>
 <div class="avatar-preview" @pointermove="followPointer">
  <Scene ref="scene" :pet-ready="true" :model-path="modelPath" :adaptor="adaptor" :profile-id="profileId" :revision="revision" :model-version="modelVersion"
   :focus-at="focus" :view-transform="{scale:1,offset:{x:0,y:0}}" @playback-change="name=>emit('playbackChange',name)" @model-ready="emit('ready')" @error="message=>emit('error',message)" />
 </div>
</template>
<script setup lang="ts">
import {ref,onBeforeUnmount} from 'vue';
import Scene from '../pet-vue/live2d/Scene.vue';
import type {AvatarAdaptor,AvatarState} from '@shared/contracts/avatar';
const props=defineProps<{modelPath:string;adaptor?:AvatarAdaptor;profileId?:string;revision?:number;modelVersion?:string}>();
const emit=defineEmits<{playbackChange:[string];validated:[{file:string;kind:'motion'|'expression';modelVersion?:string;profileId?:string}];error:[string];ready:[]}>();
const scene=ref<InstanceType<typeof Scene>>();
const focus=ref({x:0,y:0});
function followPointer(event:PointerEvent){const bounds=(event.currentTarget as HTMLElement).getBoundingClientRect();focus.value={x:event.clientX-bounds.left,y:event.clientY-bounds.top};}
let disposed=false;onBeforeUnmount(()=>{disposed=true;});
async function validate(file:string,kind:'motion'|'expression'){if(!scene.value)throw new Error('模型尚未加载');const owner=props.profileId,version=props.modelVersion,current=scene.value;await current.validate(file,kind);if(disposed||current!==scene.value||owner!==props.profileId||version!==props.modelVersion)throw new Error('预览验证结果已过期');emit('validated',{file,kind,modelVersion:version,profileId:owner});}
defineExpose({play:async(input:{motion?:string;expression?:string;loop?:boolean})=>{if(input.motion)await validate(input.motion,'motion');if(input.expression)await validate(input.expression,'expression');return scene.value?.play(input);},stop:()=>scene.value?.stop(),setState:(state:AvatarState)=>scene.value?.setState(state),validate,diagnostics:()=>scene.value?.diagnostics()});
</script>
<style scoped>.avatar-preview{height:400px;min-height:300px;position:relative;overflow:hidden}.avatar-preview :deep(.desktop-pet-stage){height:100%}</style>
