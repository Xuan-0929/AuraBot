/**
 * 在数据库迁移提交后幂等清理旧 Task Runtime 的两个受控 userData 子项。
 *
 * 本模块只处理文件系统，不参与 SQLite 事务：部分删除不会恢复，失败由数据库启动
 * 边界向上抛出并在下次启动重试。候选路径必须是规范 userData 的精确直接子项，
 * 符号链接只解除链接；可注入 entry remover 仅用于验证部分失败语义。
 */

import { lstatSync, realpathSync, rmSync, unlinkSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";

const LEGACY_TASK_DIRECTORIES = ["task-inputs", "task-agent-output"] as const;

export type LegacyTaskEntryRemover = (
  candidate: string,
  directoryName: (typeof LEGACY_TASK_DIRECTORIES)[number]
) => void;

export interface LegacyTaskCleanupOptions {
  entryRemover?: LegacyTaskEntryRemover;
}

export function cleanupLegacyTaskDirectories(
  userDataPath: string,
  options: LegacyTaskCleanupOptions = {}
): void {
  const canonicalRoot = realpathSync(resolve(userDataPath));
  const entryRemover = options.entryRemover ?? removeLegacyTaskEntry;

  for (const directoryName of LEGACY_TASK_DIRECTORIES) {
    const candidate = resolve(join(canonicalRoot, directoryName));
    if (
      dirname(candidate) !== canonicalRoot ||
      basename(candidate) !== directoryName
    ) {
      throw new Error("Task runtime cleanup rejected an unsafe path");
    }

    try {
      lstatSync(candidate);
      entryRemover(candidate, directoryName);
    } catch (error) {
      if (isMissingPathError(error)) {
        continue;
      }
      const detail = error instanceof Error ? error.message : String(error);
      throw new Error(
        `Task runtime cleanup failed for ${directoryName}: ${detail}`,
        { cause: error }
      );
    }
  }
}

function removeLegacyTaskEntry(candidate: string): void {
  const stat = lstatSync(candidate);
  if (stat.isSymbolicLink()) {
    unlinkSync(candidate);
    return;
  }
  rmSync(candidate, { force: true, recursive: true });
}

function isMissingPathError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    error.code === "ENOENT"
  );
}
