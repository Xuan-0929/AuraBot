import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createAppDatabase } from "../../../src/core/storage/db";

const tempDirs: string[] = [];

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { force: true, recursive: true });
  }
});

describe("createAppDatabase", () => {
  it("initializes core, task, and memory tables in a fresh SQLite file", () => {
    const dir = mkdtempSync(join(tmpdir(), "aurabot-db-"));
    tempDirs.push(dir);

    const database = createAppDatabase({ filePath: join(dir, "aurabot.sqlite") });
    const tables = database
      .prepare("select name from sqlite_master where type = 'table' order by name")
      .all()
      .map((row) => (row as { name: string }).name);

    database.close();

    expect(tables).toEqual(
      expect.arrayContaining([
        "profiles",
        "souls",
        "sessions",
        "messages",
        "tasks",
        "memory_nodes",
        "memory_edges",
        "memory_summaries",
        "memory_write_queue",
        "event_logs",
        "schema_migrations"
      ])
    );
  });
});
