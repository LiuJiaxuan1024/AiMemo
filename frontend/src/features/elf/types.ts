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

export type ElfEventSource = "jobs" | "chat" | "memory" | "graph" | "workshop" | "system";

export interface ElfEvent {
  id?: string;
  source: ElfEventSource;
  mood: ElfMood;
  motion?: ElfMotion;
  message?: string;
  priority: number;
  ttlMs?: number;
  createdAt?: number;
  dedupeKey?: string;
  metadata?: Record<string, unknown>;
}

export interface ElfState {
  mood: ElfMood;
  message: string;
  source: ElfEventSource;
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

export type ElfRuntimeStatus =
  | "idle"
  | "thinking"
  | "tool_running"
  | "streaming_answer"
  | "speaking"
  | "waiting_user_input"
  | "completed"
  | "failed"
  | "recovering";

export interface ElfRuntimeStatusRead {
  status: ElfRuntimeStatus;
  busy: boolean;
  conversation_id?: number | null;
  turn_id?: number | null;
  pending_interrupt?: Record<string, unknown> | null;
  last_message: string;
  last_bubbles: Array<Record<string, unknown>>;
  last_error: string;
  message: string;
  updated_at: string;
}
