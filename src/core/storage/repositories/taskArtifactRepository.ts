import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import type { AppDatabase } from "../db";

export interface TaskArtifact {
  id: string;
  taskId: string;
  artifactType: string;
  uri: string;
  createdAt: string;
}

interface TaskArtifactRow {
  id: string;
  task_id: string;
  artifact_type: string;
  uri: string;
  created_at: string;
}

export class TaskArtifactRepository {
  constructor(private readonly database: AppDatabase) {}

  create(input: { taskId: string; artifactType: string; uri: string }): TaskArtifact {
    const artifact: TaskArtifact = {
      id: `task_artifact_${randomUUID()}`,
      taskId: input.taskId,
      artifactType: input.artifactType,
      uri: input.uri,
      createdAt: new Date().toISOString()
    };

    this.database
      .prepare(
        `
        insert into task_artifacts (id, task_id, artifact_type, uri, created_at)
        values (@id, @taskId, @artifactType, @uri, @createdAt)
      `
      )
      .run(artifact);

    return artifact;
  }

  listByTask(taskId: string): TaskArtifact[] {
    return (
      this.database
        .prepare("select * from task_artifacts where task_id = ? order by created_at asc")
        .all(taskId) as TaskArtifactRow[]
    ).map(mapTaskArtifact);
  }

  isUriAllowed(taskId: string, uri: string): boolean {
    const normalizedUri = resolve(uri);

    return this.listByTask(taskId).some((artifact) => resolve(artifact.uri) === normalizedUri);
  }
}

function mapTaskArtifact(row: TaskArtifactRow): TaskArtifact {
  return {
    id: row.id,
    taskId: row.task_id,
    artifactType: row.artifact_type,
    uri: row.uri,
    createdAt: row.created_at
  };
}
