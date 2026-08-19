/**
 * 创建 Electron 主进程拥有的 SQLite 数据库并原子应用 schema migrations。
 *
 * 调用方必须同时提供数据库文件和规范 userData 根目录；迁移 context 只允许旧 Task
 * 清理操作触达该根目录下两个固定子项，失败时关闭数据库并阻止应用启动。
 */

import { mkdirSync, realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";
import Database from "better-sqlite3";
import { MIGRATIONS, type MigrationContext } from "./migrations";

export type AppDatabase = Database.Database;

export interface CreateAppDatabaseOptions {
  filePath: string;
  userDataPath: string;
}

export function createAppDatabase(options: CreateAppDatabaseOptions): AppDatabase {
  mkdirSync(dirname(options.filePath), { recursive: true });
  mkdirSync(options.userDataPath, { recursive: true });

  const database = new Database(options.filePath);
  database.pragma("foreign_keys = ON");

  try {
    applyMigrations(database, {
      userDataPath: realpathSync(resolve(options.userDataPath))
    });
    return database;
  } catch (error) {
    database.close();
    throw error;
  }
}

function applyMigrations(database: AppDatabase, context: MigrationContext): void {
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

  database.transaction(() => {
    for (const migration of MIGRATIONS) {
      if (appliedIds.has(migration.id)) {
        continue;
      }

      if (migration.up.trim()) {
        database.exec(migration.up);
      }
      migration.after?.(database, context);
      database.prepare(
        "insert into schema_migrations (id, name, applied_at) values (?, ?, ?)"
      ).run(migration.id, migration.name, new Date().toISOString());
    }
  })();
}
