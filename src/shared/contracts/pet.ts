export type PetActivityKind = "task_completed" | "voice_reply" | "voice_notice" | "memory_follow_up";

export interface PetActivity {
  id: string;
  kind: PetActivityKind;
  message: string;
  createdAt: string;
}
