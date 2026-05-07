export interface Live2DModelDescriptor {
  id: string;
  displayName: string;
  folderName: string;
  manifestFileName: string;
  manifestPath: string;
}

export interface Live2DModelState {
  currentModelId: string;
  currentModelPath: string;
  models: Live2DModelDescriptor[];
}
