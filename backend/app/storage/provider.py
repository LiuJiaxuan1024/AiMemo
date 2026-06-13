from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class StorageError(RuntimeError):
    """Base class for storage provider failures."""


class StorageNotFoundError(StorageError):
    """Raised when a requested object does not exist."""


class StorageConfigurationError(StorageError):
    """Raised when the configured storage provider cannot be created."""


class StorageAuthError(StorageError):
    """Raised when object storage rejects credentials or permissions."""


@dataclass(frozen=True)
class StorageObjectMetadata:
    key: str
    size_bytes: int
    etag: str
    content_type: str
    last_modified: datetime | None = None
    storage_class: str | None = None


@dataclass(frozen=True)
class PresignedUrl:
    url: str
    expires_at: datetime
    method: str


class CloudObjectStorageProvider(Protocol):
    def get_bytes(self, key: str) -> bytes:
        ...

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> StorageObjectMetadata:
        ...

    def head_object(self, key: str) -> StorageObjectMetadata | None:
        ...

    def delete_object(self, key: str) -> None:
        ...

    def list_objects(self, prefix: str, *, limit: int = 1000) -> list[StorageObjectMetadata]:
        ...

    def create_upload_url(self, key: str, *, content_type: str, expires_seconds: int) -> PresignedUrl:
        ...

    def create_download_url(self, key: str, *, expires_seconds: int) -> PresignedUrl:
        ...
