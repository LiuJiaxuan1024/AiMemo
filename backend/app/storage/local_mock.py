from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.storage.provider import PresignedUrl, StorageNotFoundError, StorageObjectMetadata


class LocalMockStorageProvider:
    """Filesystem-backed object storage for local development and tests."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def get_bytes(self, key: str) -> bytes:
        path = self._path_for_key(key)
        if not path.exists() or not path.is_file():
            raise StorageNotFoundError(f"Object not found: {key}")
        return path.read_bytes()

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> StorageObjectMetadata:
        path = self._path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        sidecar = self._metadata_path(path)
        sidecar.write_text(
            json.dumps(
                {
                    "content_type": content_type,
                    "metadata": metadata or {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return self._metadata_for_path(key, path)

    def head_object(self, key: str) -> StorageObjectMetadata | None:
        path = self._path_for_key(key)
        if not path.exists() or not path.is_file():
            return None
        return self._metadata_for_path(key, path)

    def delete_object(self, key: str) -> None:
        path = self._path_for_key(key)
        if path.exists() and path.is_file():
            path.unlink()
        sidecar = self._metadata_path(path)
        if sidecar.exists() and sidecar.is_file():
            sidecar.unlink()

    def list_objects(self, prefix: str, *, limit: int = 1000) -> list[StorageObjectMetadata]:
        normalized_prefix = prefix.strip("/")
        results: list[StorageObjectMetadata] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.name.endswith(".metadata.json"):
                continue
            key = path.relative_to(self.root).as_posix()
            if key.startswith(normalized_prefix):
                results.append(self._metadata_for_path(key, path))
                if len(results) >= limit:
                    break
        return results

    def create_upload_url(self, key: str, *, content_type: str, expires_seconds: int) -> PresignedUrl:
        return PresignedUrl(
            url=self._path_for_key(key).as_uri(),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_seconds),
            method="PUT",
        )

    def create_download_url(self, key: str, *, expires_seconds: int) -> PresignedUrl:
        return PresignedUrl(
            url=self._path_for_key(key).as_uri(),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_seconds),
            method="GET",
        )

    def _path_for_key(self, key: str) -> Path:
        clean_key = key.strip().replace("\\", "/").strip("/")
        if not clean_key or ".." in clean_key.split("/"):
            raise ValueError(f"Invalid object key: {key!r}")
        path = (self.root / clean_key).resolve()
        if self.root not in path.parents and path != self.root:
            raise ValueError(f"Object key escapes storage root: {key!r}")
        return path

    def _metadata_for_path(self, key: str, path: Path) -> StorageObjectMetadata:
        stat = path.stat()
        data = path.read_bytes()
        content_type = "application/octet-stream"
        sidecar = self._metadata_path(path)
        if sidecar.exists():
            try:
                content_type = str(json.loads(sidecar.read_text(encoding="utf-8")).get("content_type") or content_type)
            except Exception:
                content_type = "application/octet-stream"
        return StorageObjectMetadata(
            key=key,
            size_bytes=stat.st_size,
            etag=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
            last_modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            storage_class="Standard",
        )

    @staticmethod
    def _metadata_path(path: Path) -> Path:
        return path.with_name(path.name + ".metadata.json")
