<!-- Edits only the enclosing Profile voice draft, configuring Volcengine Pipeline/Realtime controls.
     The parent owns dirty/save/apply and secret-clear intent across tabs; Main owns credentials.
     Resource preparation fills Profile paths, while downloaded asset files remain shared. -->
<template>
              <section class="settings-page-section">
<p v-if="error" role="alert">{{ error }}</p>
                <p class="settings-hint">{{ t(displayLanguage, 'voiceCaptureHint') }}</p>
                <div class="settings-page-field">
                  <span>{{ SPEECH_TEXT[displayLanguage].voiceMode }}</span>
                  <div class="voice-mode-control">
                    <span :class="{selected:voice.speech.mode==='pipeline'}">Pipeline</span>
                    <button type="button" role="switch" name="voice-mode" class="voice-mode-switch" :aria-label="SPEECH_TEXT[displayLanguage].voiceMode + ': Realtime'" :aria-checked="voice.speech.mode==='realtime'" @click="voice.speech.mode=voice.speech.mode==='pipeline'?'realtime':'pipeline'"><span aria-hidden="true" /></button>
                    <span :class="{selected:voice.speech.mode==='realtime'}">Realtime</span>
                  </div>
                </div>
                <p class="settings-hint">{{ SPEECH_TEXT[displayLanguage].voiceModeHint }}</p>
                <Transition name="voice-details" mode="out-in">
                <div :key="voice.speech.mode" class="voice-details">
                <template v-if="voice.speech.mode==='realtime'">
                  <label class="settings-page-field"><span>SeedDuplex API Key</span><SecretInput v-model="voice.speech.realtime.api_key" :display-language="displayLanguage" :preview="detail.secretPreviews?.['speech.realtime.api_key']" :cleared="clearSecrets.includes('speech.realtime.api_key')" @update:model-value="emit('secret', 'speech.realtime.api_key', $event)" /></label>
                  <div class="settings-field-help"><button type="button" @click="openVoiceDocumentation('seeduplex-key')">{{ SPEECH_TEXT[displayLanguage].apiKeyGuide }} <span aria-hidden="true">↗</span></button></div>
                  <label class="settings-page-field"><span>{{ t(displayLanguage, 'voiceTone') }} ID</span><input v-model="voice.speech.realtime.voice" name="realtime-voice" autocomplete="off" /></label>
                  <div class="settings-field-help"><button type="button" @click="openVoiceDocumentation('seeduplex-voices')">{{ SPEECH_TEXT[displayLanguage].voiceGuide }} <span aria-hidden="true">↗</span></button></div>
                  <p class="settings-hint">{{ SPEECH_TEXT[displayLanguage].realtimeVoiceHint }}</p>
                </template>
                <template v-else>
                <article class="settings-model-card">
                  <h2>{{ t(displayLanguage, 'chatModel') }}</h2>

                  <label class="settings-page-field"><span>{{ t(displayLanguage, 'apiAddress') }}</span><input v-model="voice.chat_model.base_url" placeholder="https://…/v1" autocomplete="off" /></label>
                  <label class="settings-page-field"><span>{{ t(displayLanguage, 'modelName') }}</span><input v-model="voice.chat_model.model_name" autocomplete="off" /></label>
                  <label class="settings-page-field"><span>{{ t(displayLanguage, 'apiKey') }}</span><SecretInput v-model="voice.chat_model.api_key" :display-language="displayLanguage" :preview="detail.secretPreviews?.['chat_model.api_key']" :cleared="clearSecrets.includes('chat_model.api_key')" @update:model-value="emit('secret', 'chat_model.api_key', $event)" /></label>
                  <details class="settings-advanced"><summary>{{ t(displayLanguage, 'advancedConnectionSettings') }}</summary><label class="settings-page-field"><span>{{ t(displayLanguage, 'requestTimeoutSeconds') }}</span><input v-model.number="voice.chat_model.timeout_seconds" type="number" min="1" max="600" /></label></details>
                </article>
                <label class="settings-page-field"><span>{{ SPEECH_TEXT[displayLanguage].pipelineApiKey }}</span><SecretInput v-model="voice.speech.api_key" :display-language="displayLanguage" :preview="detail.secretPreviews?.['speech.api_key']" :cleared="clearSecrets.includes('speech.api_key')" @update:model-value="emit('secret', 'speech.api_key', $event)" /></label>
                <div class="settings-field-help"><button type="button" @click="openVoiceDocumentation('volcengine-key')">{{ SPEECH_TEXT[displayLanguage].apiKeyGuide }} <span aria-hidden="true">↗</span></button></div>
                <label class="settings-page-field"><span>{{ t(displayLanguage, 'voiceTone') }}</span><input v-model="voice.speech.tts_voice" /></label>
                <div class="settings-field-help"><button type="button" @click="openVoiceDocumentation('volcengine-voices')">{{ SPEECH_TEXT[displayLanguage].voiceGuide }} <span aria-hidden="true">↗</span></button></div>
                <p class="settings-hint">{{ SPEECH_TEXT[displayLanguage].pipelineVoiceHint }}</p>
                <SpeechResources :display-language="displayLanguage" :applied="applied" @prepared="Object.assign(voice.speech,$event)" />
                <details class="settings-advanced"><summary>{{ t(displayLanguage, 'voiceModelsAndResources') }}</summary><label v-for="field in speechFields" :key="field.key" class="settings-page-field"><span>{{ field.key==='stt_model_name'?SPEECH_TEXT[displayLanguage].sttResourceId:field.key==='tts_model_name'?SPEECH_TEXT[displayLanguage].ttsResourceId:t(displayLanguage, field.labelKey) }}</span><input v-model="voice.speech[field.key]" /></label></details>
                </template>
                </div>
                </Transition>
              </section>
</template>
<script setup lang="ts">
import {ref} from 'vue';
import {t,type DisplayLanguage,type UiTextKey} from '../../i18n';
import type {ProfileDetail} from '../../shared/contracts/profiles';
import type {ProfileVoice} from '../../shared/contracts/voice';
import type {VoiceDocumentation} from '../../shared/contracts/ipc';
import SecretInput from '../settings/SecretInput.vue';
import SpeechResources from '../settings/SpeechResources.vue';
import {SPEECH_TEXT} from '../settings/speechText';
const props=defineProps<{voice:ProfileVoice;detail:ProfileDetail;clearSecrets:string[];displayLanguage:DisplayLanguage;applied:boolean}>();
const emit=defineEmits<{secret:[path:string,value:string]}>();
const error=ref('');
async function openVoiceDocumentation(document:VoiceDocumentation){
 try{await window.aurabot.openVoiceDocumentation(document);}
 catch{error.value=SPEECH_TEXT[props.displayLanguage].openDocumentationFailed;}
}
const speechFields:Array<{key:'stt_model_name'|'tts_model_name'|'tts_voice'|'silero_model_path'|'silero_license_path'|'smart_turn_model_path'|'smart_turn_license_path';labelKey:UiTextKey}>=[
 {key:'stt_model_name',labelKey:'sttModel'},{key:'tts_model_name',labelKey:'ttsModel'},
 {key:'silero_model_path',labelKey:'vadModelFile'},{key:'silero_license_path',labelKey:'vadLicenseFile'},
 {key:'smart_turn_model_path',labelKey:'turnModelFile'},{key:'smart_turn_license_path',labelKey:'turnLicenseFile'}
];
</script>
<style scoped>
.settings-model-card{padding:20px 0;border-bottom:1px solid var(--aura-border)}
.settings-model-card h2,.settings-page-section h2{font-size:15px;font-weight:600;margin:0 0 16px}
.settings-page-field{display:grid;grid-template-columns:minmax(130px,30%) minmax(0,1fr);align-items:center;gap:16px;margin:16px 0}
.settings-page-field>span{line-height:1.6;overflow-wrap:anywhere}
.settings-page-field input,.settings-page-field select{min-width:0}
.settings-field-help{display:grid;grid-template-columns:minmax(130px,30%) minmax(0,1fr);gap:16px;margin:-8px 0 16px}
.settings-field-help>button{grid-column:2;justify-self:start;max-width:100%;min-height:28px;padding:2px 0;border:0;background:transparent;color:var(--aura-accent);font-size:12px;font-weight:400;text-align:left}
.voice-mode-control{display:flex;align-items:center;gap:12px;min-height:44px;color:var(--aura-muted)}
.voice-mode-control>span{font-weight:600;transition:color .2s ease}
.voice-mode-control>.selected{color:var(--aura-text)}
.voice-mode-switch{display:flex;align-items:center;justify-content:flex-start;flex:none;width:52px;height:32px;min-height:0;padding:3px;border:0;border-radius:999px;background:var(--aura-border);cursor:pointer;transition:background-color .2s ease}
.voice-mode-switch:hover:not(:disabled){background:var(--aura-border)}
.voice-mode-switch[aria-checked=true],.voice-mode-switch[aria-checked=true]:hover:not(:disabled){background:var(--aura-accent)}
.voice-mode-switch>span{display:block;width:26px;height:26px;border-radius:50%;background:#fff;box-shadow:0 1px 3px #0003;transition:transform .2s ease;pointer-events:none}
.voice-mode-switch[aria-checked=true]>span{transform:translateX(20px)}
.voice-mode-switch:focus-visible{outline:2px solid var(--aura-accent);outline-offset:4px}
.voice-details-enter-active{transition:opacity .16s ease-out}
.voice-details-leave-active{transition:opacity .12s ease-in;pointer-events:none}
.voice-details-enter-from,.voice-details-leave-to{opacity:0}
@media(prefers-reduced-motion:reduce){.voice-mode-control>span,.voice-mode-switch,.voice-mode-switch>span,.voice-details-enter-active,.voice-details-leave-active{transition:none}}
.settings-hint,.settings-page-section p{color:var(--aura-muted);font-size:13px;line-height:1.6}
.settings-advanced{margin-top:20px;padding-top:12px;border-top:1px solid var(--aura-border)}
.settings-advanced summary{color:var(--aura-muted);cursor:pointer;padding:4px 0;font-size:13px}
.settings-page-section{max-width:800px;padding:0 8px 16px 0}.settings-page-field{display:grid!important}.settings-page-field input{width:100%}@media(max-width:800px){.settings-page-field,.settings-field-help{grid-template-columns:1fr}.settings-field-help>button{grid-column:1}}
</style>
