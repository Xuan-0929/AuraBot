import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import type { AppDatabase } from "../db";

export interface TaskInputArtifact {
  id: string;
  taskId: string;
  artifactType: "file" | "image";
  originalName: string;
  mimeType: string;
  sizeBytes: number;
  uri: string;
  createdAt: string;
}

interface TaskInputArtifactRow {
  id: string;
  task_id: string;
  artifact_type: "file" | "image";
  original_name: string;
  mime_type: string;
  size_bytes: number;
  uri: string;
  created_at: string;
}

export class TaskInputArtifactRepository {
  constructor(private readonly database: AppDatabase) {}

  create(input: {
    taskId: string;
    artifactType: "file" | "image";
    originalName: string;
    mimeType: string;
    sizeBytes: number;
    uri: string;
  }): TaskInputArtifact {
    const artifact: TaskInputArtifact = {
      id: `task_input_artifact_${randomUUID()}`,
      taskId: input.taskId,
      artifactType: input.artifactType,
      originalName: input.originalName,
      mimeType: input.mimeType,
      sizeBytes: input.sizeBytes,
      uri: input.uri,
      createdAt: new Date().toISOString()
    };

    this.database
      .prepare(
        `
        insert into task_input_artifacts (
          id, task_id, artifact_type, original_name, mime_type, size_bytes, uri, created_at
        )
        values (
          @id, @taskId, @artifactType, @originalName, @mimeType, @sizeBytes, @uri, @createdAt
        )
      `
      )
      .run(artifact);

    return artifact;
  }

  listByTask(taskId: string): TaskInputArtifact[] {
    return (
      this.database
        .prepare("select * from task_input_artifacts where task_id = ? order by created_at asc")
        .all(taskId) as TaskInputArtifactRow[]
    ).map(mapTaskInputArtifact);
  }

  isUriAllowed(taskId: string, uri: string): boolean {
    const normalizedUri = resolve(uri);

    return this.listByTask(taskId).some((artifact) => resolve(artifact.uri) === normalizedUri);
  }
}

function mapTaskInputArtifact(row: TaskInputArtifactRow): TaskInputArtifact {
  return {
    id: row.id,
    taskId: row.task_id,
    artifactType: row.artifact_type,
    originalName: row.original_name,
    mimeType: row.mime_type,
    sizeBytes: row.size_bytes,
    uri: row.uri,
    createdAt: row.created_at
  };
}
