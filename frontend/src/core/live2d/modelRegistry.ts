/** Lists bundled Live2D manifests for initial model import by Electron Main.
 * ProfileRepository owns imported packages and active model selection, not this registry.
 */
import { existsSync, readdirSync } from "node:fs";
import { basename, join } from "node:path";
import type { Live2DModelDescriptor } from "@shared/contracts/live2dModel";

export interface Live2DModelRegistry {
  listAvailableModels(): Live2DModelDescriptor[];
}

export function createLive2DModelRegistry(options: { modelRootPath: string }): Live2DModelRegistry {
  return {
    listAvailableModels() {
      return listAvailableModels(options.modelRootPath);
    }
  };
}

function listAvailableModels(modelRootPath: string): Live2DModelDescriptor[] {
  if (!existsSync(modelRootPath)) {
    return [];
  }

  return readdirSync(modelRootPath, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .flatMap((entry) => {
      const folderPath = join(modelRootPath, entry.name);
      const manifestFileName = readdirSync(folderPath).find((fileName) => fileName.endsWith(".model3.json"));

      if (!manifestFileName) {
        return [];
      }

      return [
        {
          id: entry.name,
          displayName: basename(manifestFileName, ".model3.json"),
          folderName: entry.name,
          manifestFileName,
          manifestPath: `assets/live2d/${entry.name}/${manifestFileName}`
        }
      ];
    })
    .sort((left, right) => left.id.localeCompare(right.id));
}
