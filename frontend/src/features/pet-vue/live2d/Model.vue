<!-- Holds one resident AvatarPlayer and applies existing placement/hit-testing to its renderer.
     Only app/model identity changes replace the model. Configuration/generation updates are forwarded
     explicitly; placement, focus, hover and semantic delivery never rebuild GPU resources. -->
<template><div class="live2d-vue-model-anchor" aria-hidden="true" :data-avatar-state="status" :data-motion-starts="starts" :data-cue-drops="drops" /></template>
<!-- Configuration watches exclude human-readable labels, so typing a name never restarts playback. -->
<script setup lang="ts">
import {onBeforeUnmount,watch,ref} from 'vue';
import type {Application} from 'pixi.js';
import type {AvatarAdaptor,AvatarState} from '@shared/contracts/avatar';
import {calculateLive2DPlacement} from './live2dPlacement';
import type {Live2DSize,Live2DViewTransform} from '../../live2d/live2dDiagnostics';
import {calculateFocusedLive2DViewTransform} from '../../live2d/live2dViewScale';
import {createAvatarPlayer,avatarInstances,type AvatarPlayer} from '../../live2d/player';
import type {PerformanceEvent} from '../../live2d/performance';
import {normalizeLive2DHitAreaNames,renderLive2DFrame} from '../../live2d/runtime';
const props=defineProps<{app:Application|null;modelPath:string;adaptor?:AvatarAdaptor;profileId?:string;revision?:number;modelVersion?:string;generation?:number;focusAt:{x:number;y:number};canvasSize:{width:number;height:number};pointerAt?:{x:number;y:number};viewTransform:Live2DViewTransform;zoomRequest?:{id:number;deltaY:number;focalPoint:{x:number;y:number}}}>();
const emit=defineEmits<{playbackChange:[string];interactiveChange:[boolean];modelReady:[];error:[string];viewTransformChange:[Live2DViewTransform]}>();
let player:AvatarPlayer|undefined;let disposed=false;let loadGeneration=0;let configurationGeneration=0;let natural:Live2DSize|undefined;
const pendingReady=new Set<(available:boolean,error?:string)=>void>();
const status=ref('loading');const loadError=ref('');const starts=ref(0),drops=ref(0);
const config=()=>({onPlaybackChange:(name:string)=>emit('playbackChange',name),modelPath:props.modelPath,adaptor:props.adaptor,profileId:props.profileId,revision:props.revision,modelVersion:props.modelVersion,generation:props.generation});
watch(()=>[props.app,props.modelPath] as const,async([app,path])=>{
 const generation=++loadGeneration;configurationGeneration++;for(const finish of pendingReady)finish(false,'模型已替换');player?.dispose();player=undefined;natural=undefined;loadError.value='';status.value=path?'loading':'unconfigured';
 if(!app||!path)return;
 let configuration=0;
 try{
  const next=await createAvatarPlayer(app,config(),message=>{if(!disposed&&generation===loadGeneration){loadError.value=message;emit('error',message);}});
  if(disposed||generation!==loadGeneration){next.dispose();return;}
  player=next;next.model.anchor.set(0.5,0.95);natural={width:next.model.internalModel.width,height:next.model.internalModel.height};resize();next.model.focus(props.focusAt.x,props.focusAt.y);
  configuration=++configurationGeneration;
  await next.configure(config());if(disposed||generation!==loadGeneration||player!==next||configuration!==configurationGeneration)return;
  await next.prepare();if(disposed||generation!==loadGeneration||player!==next||configuration!==configurationGeneration)return;
  next.setGeneration(props.generation??0);status.value='ready';emit('modelReady');
 }catch(error){if(!disposed&&generation===loadGeneration&&(configuration===0||configuration===configurationGeneration)){status.value='error';loadError.value=error instanceof Error?error.message:'模型加载失败';emit('error',loadError.value);}}
},{immediate:true});
watch([()=>props.revision,()=>JSON.stringify(props.adaptor?{...props.adaptor,entries:Object.fromEntries(Object.entries(props.adaptor.entries).map(([id,{display_name,description,...playback}])=>[id,playback]))}:undefined)],async()=>{
 const current=player;if(!current)return;const generation=loadGeneration,configuration=++configurationGeneration;status.value='loading';loadError.value='';
 const active=()=>!disposed&&generation===loadGeneration&&configuration===configurationGeneration&&player===current;
 try{await current.configure(config());if(active()){status.value='ready';emit('modelReady');}}
 catch(error){if(active()){status.value='error';loadError.value=String(error);emit('error',loadError.value);}}
});
watch(()=>props.generation,value=>player?.setGeneration(value??0));
watch(()=>props.canvasSize,resize,{deep:true});watch(()=>props.viewTransform,resize);
watch(()=>props.focusAt,point=>player?.model.focus(point.x,point.y),{deep:true});
watch(()=>props.pointerAt,point=>{const model=player?.model;emit('interactiveChange',Boolean(model&&point&&normalizeLive2DHitAreaNames(model.internalModel?.hitAreas).some(name=>model.hitTest(name,point.x,point.y))));},{deep:true});
watch(()=>props.zoomRequest,request=>{if(request&&player&&natural)emit('viewTransformChange',calculateFocusedLive2DViewTransform({hostSize:props.canvasSize,modelSize:natural,compact:false,current:props.viewTransform,focalPoint:request.focalPoint,wheelDeltaY:request.deltaY}));});
function resize(){if(!player||!natural)return;const placement=calculateLive2DPlacement({hostSize:props.canvasSize,modelSize:natural,compact:false,viewTransform:props.viewTransform});player.model.scale.set(placement.scale);player.model.x=placement.x;player.model.y=placement.y;renderLive2DFrame(props.app);}
function requirePlayer(){if(!player)throw new Error('模型尚未加载');return player;}
function ready():Promise<{available:boolean;error?:string}>{
 if(disposed)return Promise.resolve({available:false,error:'模型已关闭'});
 return new Promise(resolve=>{
  let settled=false,stop=()=>{};
  const finish=(available:boolean,error?:string)=>{if(settled)return;settled=true;clearTimeout(timer);stop();pendingReady.delete(finish);resolve({available,error});};
  const timer=setTimeout(()=>finish(false,'模型准备超时，文字与语音仍可使用'),10000);
  pendingReady.add(finish);
  stop=watch(status,value=>{if(value==='ready')finish(!loadError.value,loadError.value||undefined);else if(value==='error'||value==='unconfigured')finish(false,loadError.value||'未配置模型');},{immediate:true});
  if(settled)stop();
 });
}
defineExpose({ready,play:(input:{motion?:string;expression?:string;loop?:boolean})=>requirePlayer().play(input),stop:()=>player?.stop(),setState:(state:AvatarState)=>player?.setState(state),accept:(event:PerformanceEvent)=>{player?.accept(event);starts.value=player?.diagnostics.starts??0;drops.value=player?.diagnostics.dropped??0;},validate:async(file:string,kind:'motion'|'expression')=>{await requirePlayer().load(file,kind);},diagnostics:()=>player?{...player.diagnostics,elapsedMs:player.channel.elapsedMs,instances:{...avatarInstances}}:null});
onBeforeUnmount(()=>{disposed=true;loadGeneration++;configurationGeneration++;for(const finish of pendingReady)finish(false,'模型已关闭');player?.dispose();player=undefined;});
</script>
