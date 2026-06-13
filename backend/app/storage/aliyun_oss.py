from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from email.utils import formatdate, parsedate_to_datetime
import hashlib
import hmac
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from app.core.config import settings
from app.storage.provider import (
    CloudObjectStorageProvider,
    PresignedUrl,
    StorageAuthError,
    StorageConfigurationError,
    StorageError,
    StorageNotFoundError,
    StorageObjectMetadata,
)


class AliyunOssStorageProvider(CloudObjectStorageProvider):
    """Minimal Aliyun OSS adapter implemented with the standard library."""

    def __init__(
        self,
        *,
        bucket: str | None = None,
        endpoint: str | None = None,
        access_key_id: str | None = None,
        access_key_secret: str | None = None,
    ):
        self.bucket = (bucket if bucket is not None else settings.storage_aliyun_bucket).strip()
        self.endpoint = (endpoint if endpoint is not None else settings.storage_aliyun_endpoint).strip().rstrip("/")
        key_id_env = settings.storage_aliyun_access_key_id_env
        key_secret_env = settings.storage_aliyun_access_key_secret_env
        self.access_key_id = (access_key_id if access_key_id is not None else _read_env_secret(key_id_env)).strip()
        self.access_key_secret = (
            access_key_secret if access_key_secret is not None else _read_env_secret(key_secret_env)
        ).strip()
        if not self.bucket:
            raise StorageConfigurationError("storage.aliyun_oss.bucket is required.")
        if not self.endpoint:
            raise StorageConfigurationError("storage.aliyun_oss.endpoint is required.")
        if not self.access_key_id or not self.access_key_secret:
            raise StorageConfigurationError(
                f"{key_id_env} and {key_secret_env} must be set for aliyun_oss storage."
            )
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise StorageConfigurationError(f"Invalid OSS endpoint: {self.endpoint}")
        self.scheme = parsed.scheme
        self.host = f"{self.bucket}.{parsed.netloc}"

    def get_bytes(self, key: str) -> bytes:
        return self._request("GET", key).body

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> StorageObjectMetadata:
        headers = {"Content-Type": content_type}
        for name, value in (metadata or {}).items():
            safe_name = "".join(ch.lower() if ch.isalnum() else "-" for ch in name.strip()).strip("-")
            if safe_name:
                headers[f"x-oss-meta-{safe_name}"] = str(value)
        result = self._request("PUT", key, data=data, headers=headers, content_type=content_type)
        return StorageObjectMetadata(
            key=key,
            size_bytes=len(data),
            etag=result.headers.get("ETag", "").strip('"'),
            content_type=content_type,
            last_modified=None,
            storage_class=settings.storage_default_storage_class,
        )

    def head_object(self, key: str) -> StorageObjectMetadata | None:
        try:
            result = self._request("HEAD", key)
        except StorageNotFoundError:
            return None
        headers = result.headers
        return StorageObjectMetadata(
            key=key,
            size_bytes=int(headers.get("Content-Length") or 0),
            etag=headers.get("ETag", "").strip('"'),
            content_type=headers.get("Content-Type", "application/octet-stream"),
            last_modified=_parse_http_datetime(headers.get("Last-Modified")),
            storage_class=headers.get("x-oss-storage-class"),
        )

    def delete_object(self, key: str) -> None:
        try:
            self._request("DELETE", key)
        except StorageNotFoundError:
            return

    def list_objects(self, prefix: str, *, limit: int = 1000) -> list[StorageObjectMetadata]:
        query = {
            "prefix": prefix.strip("/"),
            "max-keys": str(max(1, min(limit, 1000))),
        }
        result = self._request("GET", "", query=query)
        root = ElementTree.fromstring(result.body)
        objects: list[StorageObjectMetadata] = []
        for contents in root.findall(".//Contents"):
            key = contents.findtext("Key") or ""
            size = int(contents.findtext("Size") or 0)
            etag = (contents.findtext("ETag") or "").strip('"')
            storage_class = contents.findtext("StorageClass")
            objects.append(
                StorageObjectMetadata(
                    key=key,
                    size_bytes=size,
                    etag=etag,
                    content_type="application/octet-stream",
                    last_modified=_parse_iso_datetime(contents.findtext("LastModified")),
                    storage_class=storage_class,
                )
            )
        return objects

    def create_upload_url(self, key: str, *, content_type: str, expires_seconds: int) -> PresignedUrl:
        return self._create_signed_url("PUT", key, expires_seconds=expires_seconds, content_type=content_type)

    def create_download_url(self, key: str, *, expires_seconds: int) -> PresignedUrl:
        return self._create_signed_url("GET", key, expires_seconds=expires_seconds, content_type="")

    def _request(
        self,
        method: str,
        key: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        content_type: str = "",
        query: dict[str, str] | None = None,
    ) -> "_OssResponse":
        date = formatdate(usegmt=True)
        request_headers = {
            "Date": date,
            "Host": self.host,
            **(headers or {}),
        }
        if content_type and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = content_type
        canonical_resource = self._canonical_resource(key, query=query)
        authorization = self._authorization(
            method=method,
            content_md5="",
            content_type=request_headers.get("Content-Type", ""),
            date=date,
            headers=request_headers,
            canonical_resource=canonical_resource,
        )
        request_headers["Authorization"] = authorization
        url = self._url_for_key(key, query=query)
        request = Request(url, data=data, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                return _OssResponse(body=response.read(), headers=response.headers)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 404:
                raise StorageNotFoundError(f"OSS object not found: {key}") from exc
            if exc.code in {401, 403}:
                raise StorageAuthError(f"OSS authorization failed ({exc.code}): {_oss_error_code(body)}") from exc
            raise StorageError(f"OSS request failed ({exc.code}): {_oss_error_code(body) or body[:200]}") from exc
        except URLError as exc:
            raise StorageError(f"OSS request failed: {exc}") from exc

    def _create_signed_url(
        self,
        method: str,
        key: str,
        *,
        expires_seconds: int,
        content_type: str,
    ) -> PresignedUrl:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_seconds)
        expires = str(int(expires_at.timestamp()))
        canonical_resource = self._canonical_resource(key)
        signature = self._signature(
            method=method,
            content_md5="",
            content_type=content_type,
            date=expires,
            headers={},
            canonical_resource=canonical_resource,
        )
        query = {
            "OSSAccessKeyId": self.access_key_id,
            "Expires": expires,
            "Signature": signature,
        }
        return PresignedUrl(
            url=self._url_for_key(key, query=query),
            expires_at=expires_at,
            method=method,
        )

    def _authorization(
        self,
        *,
        method: str,
        content_md5: str,
        content_type: str,
        date: str,
        headers: dict[str, str],
        canonical_resource: str,
    ) -> str:
        signature = self._signature(
            method=method,
            content_md5=content_md5,
            content_type=content_type,
            date=date,
            headers=headers,
            canonical_resource=canonical_resource,
        )
        return f"OSS {self.access_key_id}:{signature}"

    def _signature(
        self,
        *,
        method: str,
        content_md5: str,
        content_type: str,
        date: str,
        headers: dict[str, str],
        canonical_resource: str,
    ) -> str:
        canonical_headers = _canonical_oss_headers(headers)
        string_to_sign = "\n".join([method, content_md5, content_type, date]) + "\n"
        string_to_sign += canonical_headers + canonical_resource
        digest = hmac.new(
            self.access_key_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode("ascii")

    def _canonical_resource(self, key: str, *, query: dict[str, str] | None = None) -> str:
        resource = f"/{self.bucket}/"
        clean_key = key.strip("/")
        if clean_key:
            resource += clean_key
        subresources = _canonical_subresources(query or {})
        return f"{resource}?{subresources}" if subresources else resource

    def _url_for_key(self, key: str, *, query: dict[str, str] | None = None) -> str:
        clean_key = key.strip("/")
        path = "/" + quote(clean_key, safe="/") if clean_key else "/"
        suffix = f"?{urlencode(query)}" if query else ""
        return f"{self.scheme}://{self.host}{path}{suffix}"


class _OssResponse:
    def __init__(self, *, body: bytes, headers):
        self.body = body
        self.headers = headers


def _canonical_oss_headers(headers: dict[str, str]) -> str:
    oss_headers = []
    for name, value in headers.items():
        lowered = name.strip().lower()
        if lowered.startswith("x-oss-"):
            oss_headers.append((lowered, " ".join(str(value).strip().split())))
    oss_headers.sort(key=lambda item: item[0])
    return "".join(f"{name}:{value}\n" for name, value in oss_headers)


def _canonical_subresources(query: dict[str, str]) -> str:
    allowed = {
        "acl",
        "append",
        "callback",
        "callback-var",
        "cname",
        "comp",
        "cors",
        "delete",
        "img",
        "lifecycle",
        "live",
        "location",
        "logging",
        "objectMeta",
        "partNumber",
        "position",
        "qos",
        "referer",
        "replication",
        "replicationLocation",
        "replicationProgress",
        "response-cache-control",
        "response-content-disposition",
        "response-content-encoding",
        "response-content-language",
        "response-expires",
        "security-token",
        "status",
        "style",
        "styleName",
        "symlink",
        "uploadId",
        "uploads",
        "vod",
        "website",
        "x-oss-process",
    }
    pairs = []
    for name, value in query.items():
        if name in allowed:
            pairs.append(name if value == "" else f"{name}={value}")
    return "&".join(sorted(pairs))


def _parse_http_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except Exception:
        return None


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _oss_error_code(body: str) -> str:
    try:
        root = ElementTree.fromstring(body)
        return root.findtext("Code") or ""
    except Exception:
        return ""


def _read_env_secret(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    for path in _dotenv_candidates():
        if not path.exists() or not path.is_file():
            continue
        found = _read_dotenv_key(path, name)
        if found:
            return found
    return ""


def _dotenv_candidates() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[3]
    cwd = Path.cwd()
    return list(
        dict.fromkeys(
            [
                cwd / ".env",
                cwd / "backend" / ".env",
                cwd.parent / ".env",
                repo_root / ".env",
                repo_root / "backend" / ".env",
            ]
        )
    )


def _read_dotenv_key(path: Path, name: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return ""
    prefix = f"{name}="
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or not stripped.startswith(prefix):
            continue
        value = stripped[len(prefix) :].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        return value
    return ""
