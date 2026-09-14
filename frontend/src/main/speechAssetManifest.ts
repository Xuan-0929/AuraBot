/** Pins official Silero 6.2.1 and Smart Turn 3.2 CPU models and full upstream licenses.
 * Main downloads exactly these bytes; defaults and IPC use the same app-owned installation paths.
 * Hashes measured from the immutable upstream URLs on 2026-09-14. */
import {join} from 'node:path';
import type {SpeechAssetKey,SpeechAssetPaths} from '../shared/contracts/speechAssets';
export interface SpeechAsset {key:SpeechAssetKey;file:string;url:string;size:number;sha256:string;}
export const SPEECH_ASSET_VERSION='silero-6.2.1-smart-turn-3.2';
export const SPEECH_ASSETS:readonly SpeechAsset[]=[
  {
    "key": "silero_model_path",
    "file": "silero-vad.onnx",
    "url": "https://raw.githubusercontent.com/snakers4/silero-vad/7e30209a3e901f9842f81b225f3e93d8199902b1/src/silero_vad/data/silero_vad.onnx",
    "size": 2327524,
    "sha256": "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
  },
  {
    "key": "silero_license_path",
    "file": "silero-vad.txt",
    "url": "https://raw.githubusercontent.com/snakers4/silero-vad/7e30209a3e901f9842f81b225f3e93d8199902b1/LICENSE",
    "size": 1075,
    "sha256": "2e63e9a38b6e8fc0c7bc37ce174caca1862870856c6daf5697cfb785e925520b"
  },
  {
    "key": "smart_turn_model_path",
    "file": "smart-turn-v3.2.onnx",
    "url": "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/smart-turn-v3.2-cpu.onnx",
    "size": 8679182,
    "sha256": "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"
  },
  {
    "key": "smart_turn_license_path",
    "file": "smart-turn-v3.2.txt",
    "url": "https://raw.githubusercontent.com/pipecat-ai/smart-turn/4786657e242dfe77dd138699ac564ee074a2a543/LICENSE",
    "size": 1301,
    "sha256": "0d66364067f678c08586ebb60a16a2aed4fa081ec11057df35585759ce0e774f"
  }
];
export function speechAssetPaths(root:string):SpeechAssetPaths {
 return Object.fromEntries(SPEECH_ASSETS.map(a=>[a.key,join(root,'speech-assets',SPEECH_ASSET_VERSION,a.file)])) as SpeechAssetPaths;
}
