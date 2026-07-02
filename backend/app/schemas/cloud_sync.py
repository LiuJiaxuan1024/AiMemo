from datetime import datetime

from pydantic import BaseModel


class CloudSyncDomainStatus(BaseModel):
    domain: str
    manifest_key: str
    last_remote_revision: int = 0
    dirty_count: int = 0
    conflict_count: int = 0
    last_synced_at: datetime | None = None
    last_error: str = ""


class CloudSyncStatusRead(BaseModel):
    enabled: bool
    provider: str
    bucket: str
    endpoint: str
    user_id: str
    manifest_key: str
    last_remote_global_revision: int
    last_pull_at: datetime | None
    last_push_at: datetime | None
    dirty_note_count: int
    conflict_count: int
    last_error: str
    domains: list[CloudSyncDomainStatus] = []


class CloudSyncDomainRunResult(BaseModel):
    domain: str
    uploaded_count: int = 0
    downloaded_count: int = 0
    skipped_count: int = 0
    conflict_count: int = 0
    error_count: int = 0
    message: str = ""


class CloudSyncRunResult(BaseModel):
    status: str
    uploaded_note_count: int = 0
    downloaded_note_count: int = 0
    skipped_note_count: int = 0
    conflict_count: int = 0
    message: str = ""
    domains: list[CloudSyncDomainRunResult] = []


class CloudSyncConflictRead(BaseModel):
    id: int
    domain: str
    entity_id: str
    local_revision: int
    remote_revision: int
    conflict_type: str = "remote_changed_local_modified"
    local_summary: str
    remote_summary: str
    status: str
    resolution: str
    created_at: datetime
    updated_at: datetime


class CloudSyncBackupRead(BaseModel):
    key: str
    name: str
    size_bytes: int
    last_modified: datetime | None = None


class CloudSyncBackupCreateResult(BaseModel):
    status: str
    key: str = ""
    size_bytes: int = 0
    message: str = ""


class CloudSyncConflictResolveRequest(BaseModel):
    resolution: str = "keep_both"


class CloudSyncRepairRequest(BaseModel):
    dry_run: bool = True


class CloudSyncRepairResult(BaseModel):
    status: str = "ok"
    domain: str = "conversations"
    dry_run: bool = True
    scanned_count: int = 0
    repaired_count: int = 0
    repaired_attachment_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    message: str = ""
