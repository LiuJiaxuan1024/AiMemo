export interface Job {
  id: number;
  type: string;
  graph_name: string | null;
  thread_id: string | null;
  dedupe_key: string | null;
  lane: string;
  lock_key: string | null;
  concurrency_policy: string;
  resource_weight: number;
  status: string;
  payload: Record<string, unknown>;
  priority: number;
  attempts: number;
  max_attempts: number;
  error: string;
  locked_at: string | null;
  locked_by: string | null;
  run_after: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface JobGraph {
  job_id: number;
  graph_name: string;
  thread_id: string;
  status: string;
  next_nodes: string[];
  mermaid: string;
}
