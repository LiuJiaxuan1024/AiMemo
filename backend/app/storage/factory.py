from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.storage.aliyun_oss import AliyunOssStorageProvider
from app.storage.local_mock import LocalMockStorageProvider
from app.storage.provider import CloudObjectStorageProvider, StorageConfigurationError


@lru_cache(maxsize=1)
def get_storage_provider() -> CloudObjectStorageProvider:
    provider = settings.storage_provider.strip().lower()
    if provider == "local_mock":
        return LocalMockStorageProvider(settings.storage_local_mock_dir)
    if provider == "aliyun_oss":
        return AliyunOssStorageProvider()
    raise StorageConfigurationError(f"Unsupported storage provider: {settings.storage_provider}")
