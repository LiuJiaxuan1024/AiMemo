import type { Job } from "../jobs/types";

export type ElfMood = "idle" | "thinking" | "working" | "success" | "warning" | "error" | "talking";

export type ElfMotion =
  | "breathe"
  | "blink"
  | "nod"
  | "look"
  | "thinking"
  | "working"
  | "success"
  | "error"
  | "dragging";

export interface ElfState {
  mood: ElfMood;
  message: string;
  source: "jobs" | "chat" | "memory" | "system";
  priority: number;
  jobId?: number;
  turnId?: number;
}

export interface ElfAssistantProps {
  activeCount: number;
  failedCount: number;
  isWorkshopOpen: boolean;
  jobs: Job[];
  onToggleWorkshop: () => void;
}
