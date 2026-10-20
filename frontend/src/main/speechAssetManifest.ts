/** Pins official Silero 6.2.1 and Smart Turn 3.2 CPU models and full upstream licenses.
 * Source installation and Main share speech-assets.json to download exactly these bytes; defaults and IPC use the same app-owned installation paths.
 * Hashes measured from the immutable upstream URLs on 2026-09-14. */
import {join} from 'node:path';
import type {SpeechAssetKey,SpeechAssetPaths} from '../shared/contracts/speechAssets';
export interface SpeechAsset {key:SpeechAssetKey;file:string;url:string;size:number;sha256:string;}
import manifest from './speech-assets.json';
export const SPEECH_ASSET_VERSION=manifest.version;
export const SPEECH_ASSETS:readonly SpeechAsset[]=manifest.assets as SpeechAsset[];
export function speechAssetPaths(root:string):SpeechAssetPaths {
 return Object.fromEntries(SPEECH_ASSETS.map(a=>[a.key,join(root,'speech-assets',SPEECH_ASSET_VERSION,a.file)])) as SpeechAssetPaths;
}
