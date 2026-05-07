import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { live2dModelSettingsSchema, type Live2DModelSettings } from "../shared/schemas/live2dModel";

const SETTINGS_FILE_NAME = "live2d-model-settings.json";

export class Live2DModelStore {
  private settings: Live2DModelSettings;
  private readonly filePath: string;

  constructor(private readonly userDataPath: string) {
    this.filePath = join(userDataPath, SETTINGS_FILE_NAME);
    this.settings = this.load();
  }

  get(): Live2DModelSettings {
    return { ...this.settings };
  }

  update(patch: Partial<Live2DModelSettings>): Live2DModelSettings {
    this.settings = live2dModelSettingsSchema.parse({
      ...this.settings,
      ...patch
    });
    this.persist();

    return this.get();
  }

  private load(): Live2DModelSettings {
    if (!existsSync(this.filePath)) {
      return live2dModelSettingsSchema.parse({});
    }

    try {
      return live2dModelSettingsSchema.parse(JSON.parse(readFileSync(this.filePath, "utf8")));
    } catch {
      return live2dModelSettingsSchema.parse({});
    }
  }

  private persist(): void {
    mkdirSync(this.userDataPath, { recursive: true });
    writeFileSync(this.filePath, `${JSON.stringify(this.settings, null, 2)}\n`, "utf8");
  }
}
