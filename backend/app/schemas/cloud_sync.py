from datetime import datetime

from pydantic import BaseModel


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


class CloudSyncRunResult(BaseModel):
    status: str
    uploaded_note_count: int = 0
    downloaded_note_count: int = 0
    skipped_note_count: int = 0
    conflict_count: int = 0
    message: str = ""
