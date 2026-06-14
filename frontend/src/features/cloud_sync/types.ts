export interface CloudSyncDomainStatus {
  domain: string;
  manifest_key: string;
  last_remote_revision: number;
  dirty_count: number;
  conflict_count: number;
  last_synced_at: string | null;
  last_error: string;
}

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
  domains: CloudSyncDomainStatus[];
}

export interface CloudSyncDomainRunResult {
  domain: string;
  uploaded_count: number;
  downloaded_count: number;
  skipped_count: number;
  conflict_count: number;
  error_count: number;
  message: string;
}

export interface CloudSyncRunResult {
  status: string;
  uploaded_note_count: number;
  downloaded_note_count: number;
  skipped_note_count: number;
  conflict_count: number;
  message: string;
  domains: CloudSyncDomainRunResult[];
}

export interface CloudSyncConflict {
  id: number;
  domain: string;
  entity_id: string;
  local_revision: number;
  remote_revision: number;
  local_summary: string;
  remote_summary: string;
  status: string;
  resolution: string;
  created_at: string;
  updated_at: string;
}

export interface CloudSyncBackup {
  key: string;
  name: string;
  size_bytes: number;
  last_modified: string | null;
}

export interface CloudSyncBackupCreateResult {
  status: string;
  key: string;
  size_bytes: number;
  message: string;
}
