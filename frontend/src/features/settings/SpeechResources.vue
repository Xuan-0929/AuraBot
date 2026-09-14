<!-- Shows Main-owned asset progress independently of Backend availability.
     Completion emits only four paths into the existing unsaved settings draft; credentials stay untouched. -->
<template>
 <article class="speech-resources">
  <h2>{{ t(displayLanguage,'speechResources') }}</h2>
  <ul v-if="state"><li v-for="asset in state.assets" :key="asset.key">{{ t(displayLanguage,labels[asset.key]) }} — {{ t(displayLanguage,asset.ready?'speechReady':'speechMissing') }}</li></ul>
  <div v-if="state?.phase==='downloading'" role="status">
   <label for="speech-download">{{ t(displayLanguage,'speechPreparing') }} {{ Math.floor(state.received/state.total*100) }}%</label>
   <progress id="speech-download" :value="state.received" :max="state.total" />
   <span>{{ (state.received/1048576).toFixed(1) }} / {{ (state.total/1048576).toFixed(1) }} MB</span>
  </div>
  <p v-if="message" :role="state?.error?'alert':'status'">{{ message }}</p>
  <button type="button" :disabled="starting||state?.phase==='downloading'" @click="prepare">{{ t(displayLanguage,'speechPrepare') }}</button>
  <button v-if="state?.phase==='downloading'" type="button" @click="cancel">{{ t(displayLanguage,'cancel') }}</button>
 </article>
</template>
<script setup lang="ts">
import {computed,onMounted,onBeforeUnmount,ref} from 'vue';
import {t,type DisplayLanguage,type UiTextKey} from '../../i18n';
import type {SpeechAssetsState,SpeechAssetKey,SpeechAssetPaths} from '../../shared/contracts/speechAssets';
const props=defineProps<{displayLanguage:DisplayLanguage;applied?:boolean}>();
const emit=defineEmits<{prepared:[paths:SpeechAssetPaths]}>();
let adopted=false;
function adopt(value:SpeechAssetsState){if(!adopted){adopted=true;emit('prepared',value.paths);}}
const state=ref<SpeechAssetsState>();const starting=ref(false);const error=ref('');
const labels:Record<SpeechAssetKey,UiTextKey>={silero_model_path:'vadModelFile',silero_license_path:'vadLicenseFile',smart_turn_model_path:'turnModelFile',smart_turn_license_path:'turnLicenseFile'};
const message=computed(()=>error.value||(state.value?.phase==='ready'?t(props.displayLanguage,props.applied?'speechReady':'speechPrepared'):state.value?.phase==='cancelled'?t(props.displayLanguage,'speechCancelled'):state.value?.error?t(props.displayLanguage,({network:'speechDownloadNetwork',integrity:'speechDownloadIntegrity',filesystem:'speechDownloadFilesystem'} as const)[state.value.error]):''));
let unsubscribe:(()=>void)|undefined;
onMounted(async()=>{unsubscribe=window.aurabot.onSpeechAssetsChanged(value=>{const wasDownloading=state.value?.phase==='downloading';state.value=value;if(value.phase==='downloading')adopted=false;if(wasDownloading&&value.phase==='ready')adopt(value);});try{state.value=await window.aurabot.getSpeechAssets();}catch{error.value=t(props.displayLanguage,'speechDownloadFilesystem');}});
onBeforeUnmount(()=>unsubscribe?.());
async function prepare(){adopted=false;starting.value=true;error.value='';try{state.value=await window.aurabot.prepareSpeechAssets();if(state.value.phase==='ready')adopt(state.value);}catch{error.value=t(props.displayLanguage,'speechDownloadNetwork');}finally{starting.value=false;}}
async function cancel(){try{state.value=await window.aurabot.cancelSpeechAssets();}catch{error.value=t(props.displayLanguage,'speechDownloadFilesystem');}}
</script>
<style scoped>
.speech-resources{margin:24px 0;padding:16px;border:1px solid var(--aura-border);border-radius:12px}.speech-resources h2{font-size:15px}.speech-resources li{margin:8px 0}.speech-resources progress{display:block;width:100%;margin:8px 0}.speech-resources button{margin-right:8px}.speech-resources p,.speech-resources span{font-size:13px}
</style>
