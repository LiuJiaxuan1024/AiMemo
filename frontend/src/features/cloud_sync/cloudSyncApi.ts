import type {
  CloudSyncBackup,
  CloudSyncBackupCreateResult,
  CloudSyncConflict,
  CloudSyncDomainStatus,
  CloudSyncRunResult,
  CloudSyncStatus,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function getCloudSyncStatus(): Promise<CloudSyncStatus> {
  return request<CloudSyncStatus>("/api/cloud-sync/status");
}

export function pullCloudSync(): Promise<CloudSyncRunResult> {
  return request<CloudSyncRunResult>("/api/cloud-sync/pull", { method: "POST" });
}

export function pushCloudSync(): Promise<CloudSyncRunResult> {
  return request<CloudSyncRunResult>("/api/cloud-sync/push", { method: "POST" });
}

export function runCloudSync(): Promise<CloudSyncRunResult> {
  return request<CloudSyncRunResult>("/api/cloud-sync/sync", { method: "POST" });
}

export function listCloudSyncDomains(): Promise<CloudSyncDomainStatus[]> {
  return request<CloudSyncDomainStatus[]>("/api/cloud-sync/domains");
}

export function syncCloudSyncDomain(domain: string): Promise<CloudSyncRunResult> {
  return request<CloudSyncRunResult>(`/api/cloud-sync/domains/${encodeURIComponent(domain)}/sync`, { method: "POST" });
}

export function listCloudSyncConflicts(): Promise<CloudSyncConflict[]> {
  return request<CloudSyncConflict[]>("/api/cloud-sync/conflicts");
}

export function resolveCloudSyncConflict(conflictId: number): Promise<CloudSyncConflict> {
  return request<CloudSyncConflict>(`/api/cloud-sync/conflicts/${conflictId}/resolve`, {
    method: "POST",
    body: JSON.stringify({ resolution: "keep_both" }),
  });
}

export function listCloudSyncBackups(): Promise<CloudSyncBackup[]> {
  return request<CloudSyncBackup[]>("/api/cloud-sync/backups");
}

export function createCloudSyncBackup(): Promise<CloudSyncBackupCreateResult> {
  return request<CloudSyncBackupCreateResult>("/api/cloud-sync/backups", { method: "POST" });
}
