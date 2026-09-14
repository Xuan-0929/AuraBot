// @vitest-environment jsdom
/** Verifies the real settings resource panel exposes progress and adopts only completed paths.
 * Desktop IPC is replaced with a controlled download; no settings save or cloud request is performed. */
import {createApp,nextTick} from 'vue';
import {afterEach,expect,it,vi} from 'vitest';
import SpeechResources from '../../../src/features/settings/SpeechResources.vue';
import {publicError} from '../../../src/main-agent-realtime/client';
import type {SpeechAssetsState} from '../../../src/shared/contracts/speechAssets';
let cleanup=()=>{};afterEach(()=>cleanup());
it('shows progress, then emits paths without saving settings',async()=>{
 const initial:SpeechAssetsState={phase:'idle',assets:[{key:'silero_model_path',ready:false}],received:0,total:100,paths:{silero_model_path:'/managed/vad',silero_license_path:'/managed/vad.txt',smart_turn_model_path:'/managed/eou',smart_turn_license_path:'/managed/eou.txt'}};
 let notify!:(state:SpeechAssetsState)=>void;let finish!:(state:SpeechAssetsState)=>void;
 const adopted=vi.fn();const saveSettings=vi.fn();
 Object.assign(window,{aurabot:{getSpeechAssets:async()=>initial,onSpeechAssetsChanged:(fn:typeof notify)=>{notify=fn;return ()=>{};},prepareSpeechAssets:()=>new Promise(r=>finish=r),saveSettings}});
 const host=document.createElement('div');document.body.append(host);
 const app=createApp(SpeechResources,{displayLanguage:'zh',onPrepared:adopted});app.mount(host);
 cleanup=()=>{app.unmount();host.remove();};await nextTick();await nextTick();
 expect(adopted).not.toHaveBeenCalled();host.querySelector('button')!.click();
 notify({...initial,phase:'downloading',received:50});await nextTick();
 expect(host.querySelector('progress')?.value).toBe(50);expect(host.textContent).toContain('50%');
 const ready={...initial,phase:'ready' as const,received:100};notify(ready);finish(ready);await nextTick();await nextTick();
 expect(adopted).toHaveBeenCalledTimes(1);expect(adopted).toHaveBeenCalledWith(initial.paths);expect(saveSettings).not.toHaveBeenCalled();
 expect(host.textContent).toContain('保存并应用');
});
it('localizes controlled asset errors in all supported languages',()=>{
 expect(publicError('speech_vad_model_asset_missing','zh')).toContain('缺少 VAD');
 expect(publicError('speech_vad_model_asset_missing','en')).toContain('VAD model missing');
 expect(publicError('speech_vad_model_asset_missing','ja')).toContain('VAD モデル');
 expect(publicError('speech_eou_optional_dependency_missing','en')).toContain('dependencies');
});
