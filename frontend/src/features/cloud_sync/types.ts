export interface CloudSyncStatus {
  enabled: boolean;
  provider: string;
  bucket: string;
  endpoint: string;
  user_id: string;
  manifest_key: string;
  last_remote_global_revision: number;
  last_pull_at: string | null;
  last_push_at: string | null;
  dirty_note_count: number;
  conflict_count: number;
  last_error: string;
}

export interface CloudSyncRunResult {
  status: string;
  uploaded_note_count: number;
  downloaded_note_count: number;
  skipped_note_count: number;
  conflict_count: number;
  message: string;
}
