import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  DEFAULT_PET_SETTINGS,
  parsePetSettings,
  type PetSettings,
  type PetSettingsPatch
} from "@shared/schemas/petSettings";

const SETTINGS_FILE_NAME = "pet-settings.json";

export class PetSettingsStore {
  private settings: PetSettings;
  private readonly filePath: string;

  constructor(private readonly userDataPath: string) {
    this.filePath = join(userDataPath, SETTINGS_FILE_NAME);
    this.settings = this.load();
  }

  get(): PetSettings {
    return { ...this.settings };
  }

  update(patch: PetSettingsPatch): PetSettings {
    this.settings = parsePetSettings({
      ...this.settings,
      ...patch
    });
    this.persist();

    return this.get();
  }

  private load(): PetSettings {
    if (!existsSync(this.filePath)) {
      return { ...DEFAULT_PET_SETTINGS };
    }

    try {
      return parsePetSettings(JSON.parse(readFileSync(this.filePath, "utf8")));
    } catch {
      return { ...DEFAULT_PET_SETTINGS };
    }
  }

  private persist(): void {
    mkdirSync(this.userDataPath, { recursive: true });
    writeFileSync(this.filePath, `${JSON.stringify(this.settings, null, 2)}\n`, "utf8");
  }
}
