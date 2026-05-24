export type BackgroundTaskStatus =
  | "running"
  | "exited"
  | "failed"
  | "killed"
  | "orphaned"
  | "unknown";

export interface BackgroundTask {
  task_id: string;
  conversation_id: number | null;
  command: string;
  cwd: string;
  pid: number | null;
  status: BackgroundTaskStatus;
  exit_code: number | null;
  kill_reason: string;
  started_at: string;
  finished_at: string | null;
}

export interface BackgroundTaskOutputLine {
  line: number;
  stream: "stdout" | "stderr";
  text: string;
}

export interface BackgroundTaskOutput {
  task_id: string;
  status: BackgroundTaskStatus;
  pid: number | null;
  exit_code: number | null;
  lines: BackgroundTaskOutputLine[];
  last_line: number;
  dropped_lines: number;
  more: boolean;
}
