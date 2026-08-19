/**
 * 创建 Electron 主进程拥有的 SQLite 数据库并应用迁移与旧文件清理。
 *
 * SQLite schema/data migrations 在一个事务中提交并完成外键检查；随后独立、幂等地
 * 清理 userData 下两个旧 Task 子项。文件失败不会回滚数据库，但会关闭连接、阻止
 * 应用启动，并在下一次初始化时重试。
 */

import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import Database from "better-sqlite3";
import {
  cleanupLegacyTaskDirectories,
  type LegacyTaskEntryRemover
} from "./legacyTaskCleanup";
import { MIGRATIONS } from "./migrations";

export type AppDatabase = Database.Database;

export interface CreateAppDatabaseOptions {
  filePath: string;
  userDataPath: string;
  legacyTaskEntryRemover?: LegacyTaskEntryRemover;
}

export function createAppDatabase(options: CreateAppDatabaseOptions): AppDatabase {
  mkdirSync(dirname(options.filePath), { recursive: true });
  mkdirSync(options.userDataPath, { recursive: true });

  const database = new Database(options.filePath);
  database.pragma("foreign_keys = ON");

  try {
    applyMigrations(database);
    cleanupLegacyTaskDirectories(options.userDataPath, {
      entryRemover: options.legacyTaskEntryRemover
    });
    return database;
  } catch (error) {
    database.close();
    throw error;
  }
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

  database.transaction(() => {
    for (const migration of MIGRATIONS) {
      if (appliedIds.has(migration.id)) {
        continue;
      }

      if (migration.up.trim()) {
        database.exec(migration.up);
      }
      migration.after?.(database);
      database.prepare(
        "insert into schema_migrations (id, name, applied_at) values (?, ?, ?)"
      ).run(migration.id, migration.name, new Date().toISOString());
    }

    const violations = database.pragma("foreign_key_check") as unknown[];
    if (violations.length > 0) {
      throw new Error("Database migration failed: foreign key violations remain");
    }
  })();
}
