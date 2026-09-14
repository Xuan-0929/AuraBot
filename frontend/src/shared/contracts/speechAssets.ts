/** Resource preparation DTOs shared by Main, preload and settings.
 * Main owns paths and downloads; renderer receives progress and explicitly adopts completed paths. */
export type SpeechAssetKey='silero_model_path'|'silero_license_path'|'smart_turn_model_path'|'smart_turn_license_path';
export type SpeechAssetPaths=Record<SpeechAssetKey,string>;
export interface SpeechAssetsState {
 phase:'idle'|'downloading'|'ready'|'cancelled'|'error';
 received:number;total:number;current?:SpeechAssetKey;
 error?:'network'|'integrity'|'filesystem';
 assets:Array<{key:SpeechAssetKey;ready:boolean}>;
 paths:SpeechAssetPaths;
}
