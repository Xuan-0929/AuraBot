import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import Database from "better-sqlite3";
import { MIGRATIONS } from "./migrations";

export type AppDatabase = Database.Database;

export interface CreateAppDatabaseOptions {
  filePath: string;
}

export function createAppDatabase(options: CreateAppDatabaseOptions): AppDatabase {
  mkdirSync(dirname(options.filePath), { recursive: true });

  const database = new Database(options.filePath);
  database.pragma("foreign_keys = ON");
  applyMigrations(database);

  return database;
}

function applyMigrations(database: AppDatabase): void {
  database.exec(`
    create table if not exists schema_migrations (
      id integer primary key,
      name text not null,
      applied_at text not null
    );
  `);

  const appliedIds = new Set(
    database
      .prepare("select id from schema_migrations")
      .all()
      .map((row) => (row as { id: number }).id)
  );

  const run = database.transaction(() => {
    for (const migration of MIGRATIONS) {
      if (appliedIds.has(migration.id)) {
        continue;
      }

      database.exec(migration.up);
      database.prepare("insert into schema_migrations (id, name, applied_at) values (?, ?, ?)").run(
        migration.id,
        migration.name,
        new Date().toISOString()
      );
    }
  });

  run();
}
