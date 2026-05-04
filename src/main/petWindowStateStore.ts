import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import type { WindowRectangle } from "./windowPlacement";

const STATE_FILE_NAME = "pet-window-state.json";

const windowRectangleSchema = z.object({
  x: z.number().finite(),
  y: z.number().finite(),
  width: z.number().finite().positive(),
  height: z.number().finite().positive()
});

const petWindowStateSchema = z.object({
  bounds: windowRectangleSchema.optional()
});

export class PetWindowStateStore {
  private bounds: WindowRectangle | undefined;
  private readonly filePath: string;

  constructor(private readonly userDataPath: string) {
    this.filePath = join(userDataPath, STATE_FILE_NAME);
    this.bounds = this.load();
  }

  getBounds(): WindowRectangle | undefined {
    return this.bounds ? { ...this.bounds } : undefined;
  }

  saveBounds(bounds: WindowRectangle): void {
    this.bounds = { ...bounds };
    this.persist();
  }

  private load(): WindowRectangle | undefined {
    if (!existsSync(this.filePath)) {
      return undefined;
    }

    try {
      const parsed = petWindowStateSchema.safeParse(JSON.parse(readFileSync(this.filePath, "utf8")));

      return parsed.success ? parsed.data.bounds : undefined;
    } catch {
      return undefined;
    }
  }

  private persist(): void {
    mkdirSync(this.userDataPath, { recursive: true });
    writeFileSync(this.filePath, `${JSON.stringify({ bounds: this.bounds }, null, 2)}\n`, "utf8");
  }
}
