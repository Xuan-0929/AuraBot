import { randomUUID } from "node:crypto";
import type { AgentStatus } from "@shared/contracts/chat";
import type { PetActivity } from "@shared/contracts/pet";
import type { Message } from "@shared/schemas/message";
import type { Task, TaskEvent } from "@shared/schemas/task";
import type { AppServices } from "@core/bootstrap/createServices";
import { buildTaskSettledMessageDraftWithVoice } from "@core/tasks/taskPresentation";

export interface TaskExecutionCoordinatorWindowPort {
  broadcastTaskChanged(task: Task): void;
  broadcastAgentStatus(status: AgentStatus): void;
  broadcastChatMessage(message: Message): void;
  broadcastPetActivity(activity: PetActivity): void;
}

export class TaskExecutionCoordinator {
  constructor(
    private readonly services: AppServices,
    private readonly windows: TaskExecutionCoordinatorWindowPort
  ) {}

  startTaskInBackground(input: { profileId: string; taskId: string }): Task | undefined {
    const startedTask = this.services.taskService.runTaskInBackground({
      profileId: input.profileId,
      taskId: input.taskId,
      onProgress: (task) => {
        this.windows.broadcastTaskChanged(task);
      },
      onSettled: (task) => {
        void this.handleSettledTask(task);
      }
    });

    if (startedTask) {
      this.windows.broadcastTaskChanged(startedTask);
    }

    return startedTask;
  }

  private async handleSettledTask(task: Task): Promise<void> {
    this.collectFileArtifacts(task.id);
    this.windows.broadcastTaskChanged(task);
    this.windows.broadcastAgentStatus("idle");
    const assistantMessage = await this.createTaskSettledMessage(task);

    this.windows.broadcastChatMessage(assistantMessage);
    const activity = await this.buildPetActivityFromTask(task);

    if (activity) {
      this.windows.broadcastPetActivity(activity);
    }
  }

  private async createTaskSettledMessage(task: Task): Promise<Message> {
    const profile = this.services.profileService.getOrSeedActiveProfile();
    const mainSession = this.services.sessionRepository.getOrCreateMainSession(profile.id);
    const events = this.services.taskService.listTaskEvents(task.id);
    const artifacts = this.services.taskArtifactRepository.listByTask(task.id);
    const draft = await buildTaskSettledMessageDraftWithVoice(task, events, artifacts, {
      soul: profile.soul,
      voiceAdapter: this.services.voiceAdapter
    });

    return this.services.messageRepository.create({
      sessionId: mainSession.id,
      profileId: profile.id,
      role: "assistant",
      content: draft.content,
      attachments: draft.attachments
    });
  }

  private async buildPetActivityFromTask(task: Task): Promise<PetActivity | undefined> {
    if (task.status !== "completed" && task.status !== "failed") {
      return undefined;
    }

    const profile = this.services.profileService.getOrSeedActiveProfile();

    return {
      id: randomUUID(),
      kind: "task_completed",
      message: await this.services.voiceAdapter.render({
        scene: "pet.task_activity",
        channel: "pet_notice",
        soul: profile.soul,
        facts: {
          taskTitle: task.title,
          status: task.status,
          resultSubject: task.title
        }
      }),
      createdAt: new Date().toISOString()
    };
  }

  private collectFileArtifacts(taskId: string): void {
    const events = this.services.taskService.listTaskEvents(taskId);

    for (const path of listTaskFileChangePaths(events)) {
      if (this.services.taskArtifactRepository.isUriAllowed(taskId, path)) {
        continue;
      }

      this.services.taskArtifactRepository.create({
        taskId,
        artifactType: "file",
        uri: path
      });
    }
  }
}

function listTaskFileChangePaths(events: TaskEvent[]): string[] {
  const paths = new Set<string>();

  for (const event of events) {
    const firstChange = isRecord(event.payload.fileChange) ? event.payload.fileChange : undefined;

    if (typeof firstChange?.path === "string") {
      paths.add(firstChange.path);
    }

    const fileChanges = Array.isArray(event.payload.fileChanges) ? event.payload.fileChanges : [];
    for (const change of fileChanges) {
      if (isRecord(change) && typeof change.path === "string") {
        paths.add(change.path);
      }
    }

    const changes = Array.isArray(event.payload.changes) ? event.payload.changes : [];
    for (const change of changes) {
      if (isRecord(change) && typeof change.path === "string") {
        paths.add(change.path);
      }
    }
  }

  return [...paths];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
