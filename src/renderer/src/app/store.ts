import { create } from "zustand";
import type { AgentStatus, AppInitialState } from "../../../shared/contracts/chat";
import type { Message } from "../../../shared/schemas/message";
import type { Profile } from "../../../shared/schemas/profile";
import type { Task } from "../../../shared/schemas/task";

interface AuraBotStore {
  profile?: Profile;
  mainSessionId?: string;
  messages: Message[];
  tasks: Task[];
  agentStatus: AgentStatus;
  loadInitialState(): Promise<void>;
  openConsoleWindow(): Promise<void>;
  sendMessage(content: string): Promise<void>;
}

export const useAuraBotStore = create<AuraBotStore>((set) => ({
  messages: [],
  tasks: [],
  agentStatus: "idle",
  async loadInitialState() {
    const state: AppInitialState = await window.aurabot.getInitialState();
    set(state);
  },
  async openConsoleWindow() {
    await window.aurabot.openConsoleWindow();
  },
  async sendMessage(content: string) {
    const trimmed = content.trim();

    if (!trimmed) {
      return;
    }

    set({ agentStatus: "thinking" });
    const result = await window.aurabot.sendChatMessage({ content: trimmed });

    set((current) => ({
      messages: [
        ...current.messages,
        result.userMessage,
        ...(result.assistantMessage ? [result.assistantMessage] : [])
      ],
      tasks: updateTasks(current.tasks, result.createdTask ?? result.updatedTask),
      agentStatus: result.agentStatus
    }));
  }
}));

function updateTasks(tasks: Task[], changedTask?: Task): Task[] {
  if (!changedTask) {
    return tasks;
  }

  const exists = tasks.some((task) => task.id === changedTask.id);

  if (!exists) {
    return [changedTask, ...tasks];
  }

  return tasks.map((task) => (task.id === changedTask.id ? changedTask : task));
}
