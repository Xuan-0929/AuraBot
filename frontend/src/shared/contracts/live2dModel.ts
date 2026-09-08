/** Shares the Profile-scoped model URL and frozen adaptor with Pet and preview renderers.
 * URLs are resolved only through Electron's controlled resource protocol; playback state stays local. */
import type {AvatarAdaptor} from './avatar';
export interface Live2DModelDescriptor {
  id: string;
  displayName: string;
  folderName: string;
  manifestFileName: string;
  manifestPath: string;
}

export interface Live2DModelState {
  profileId?:string; revision?:number; modelVersion?:string; adaptor?:AvatarAdaptor;
  currentModelId: string;
  currentModelPath: string;
  models: Live2DModelDescriptor[];
}
