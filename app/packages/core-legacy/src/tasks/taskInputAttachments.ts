import { randomUUID } from "node:crypto";
import { copyFileSync, mkdirSync, statSync } from "node:fs";
import { basename, extname, join, resolve } from "node:path";
import type { ChatInputAttachment } from "../../../shared/src/contracts/chat";
import type {
  TaskInputArtifact,
  TaskInputArtifactRepository
} from "../storage/repositories/taskInputArtifactRepository";
import type { TaskRepository } from "../storage/repositories/taskRepository";

const MAX_INPUT_ATTACHMENT_BYTES = 50 * 1024 * 1024;
const ALLOWED_MIME_PREFIXES = ["image/", "text/"];
const ALLOWED_MIME_TYPES = new Set([
  "application/json",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "text/csv"
]);

export class TaskInputAttachmentError extends Error {}

export class TaskInputAttachmentService {
  constructor(
    private readonly repository: TaskInputArtifactRepository,
    private readonly taskRepository: TaskRepository,
    private readonly inputRoot: string
  ) {}

  stageForTask(taskId: string, attachments: ChatInputAttachment[] = []): TaskInputArtifact[] {
    if (attachments.length === 0) {
      return [];
    }

    const task = this.taskRepository.getById(taskId);

    if (!task) {
      throw new TaskInputAttachmentError("任务不存在，不能关联输入附件。");
    }

    return attachments.map((attachment) => this.stageOne(taskId, attachment));
  }

  private stageOne(taskId: string, attachment: ChatInputAttachment): TaskInputArtifact {
    const sourcePath = resolve(attachment.path);
    const originalName = sanitizeFileName(attachment.name || basename(sourcePath));
    const sourceStat = statSync(sourcePath);

    if (!sourceStat.isFile()) {
      throw new TaskInputAttachmentError(`附件不是可读取文件：${originalName}`);
    }

    if (sourceStat.size > MAX_INPUT_ATTACHMENT_BYTES) {
      throw new TaskInputAttachmentError(`附件太大，暂时不能超过 50MB：${originalName}`);
    }

    const mimeType = attachment.mimeType || detectMimeType(originalName);
    const artifactType = attachment.kind === "image" || mimeType.startsWith("image/") ? "image" : "file";

    if (!isAllowedMimeType(mimeType)) {
      throw new TaskInputAttachmentError(`暂时不支持这种附件类型：${mimeType}`);
    }

    const taskInputDir = resolve(this.inputRoot, taskId);
    mkdirSync(taskInputDir, { recursive: true });
    const stagedName = `${randomUUID()}-${originalName}`;
    const stagedPath = resolve(taskInputDir, stagedName);

    if (!stagedPath.startsWith(`${taskInputDir}/`) && stagedPath !== join(taskInputDir, stagedName)) {
      throw new TaskInputAttachmentError("附件暂存路径没有通过安全校验。");
    }

    copyFileSync(sourcePath, stagedPath);

    return this.repository.create({
      taskId,
      artifactType,
      originalName,
      mimeType,
      sizeBytes: attachment.sizeBytes ?? sourceStat.size,
      uri: stagedPath
    });
  }
}

function sanitizeFileName(value: string): string {
  const name = basename(value).replace(/[^\w.\-() ]+/g, "_").trim();

  return name || "attachment";
}

function isAllowedMimeType(mimeType: string): boolean {
  return ALLOWED_MIME_TYPES.has(mimeType) || ALLOWED_MIME_PREFIXES.some((prefix) => mimeType.startsWith(prefix));
}

function detectMimeType(name: string): string {
  const extension = extname(name).toLowerCase();

  switch (extension) {
    case ".png":
      return "image/png";
    case ".jpg":
    case ".jpeg":
      return "image/jpeg";
    case ".gif":
      return "image/gif";
    case ".webp":
      return "image/webp";
    case ".pdf":
      return "application/pdf";
    case ".json":
      return "application/json";
    case ".csv":
      return "text/csv";
    case ".md":
      return "text/markdown";
    case ".txt":
      return "text/plain";
    case ".docx":
      return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    case ".xlsx":
      return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
    default:
      return "application/octet-stream";
  }
}
