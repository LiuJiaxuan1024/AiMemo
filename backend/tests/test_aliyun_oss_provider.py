from app.storage.aliyun_oss import AliyunOssStorageProvider, _read_env_secret
from app.storage.provider import StorageConfigurationError


def test_read_env_secret_falls_back_to_dotenv(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("ALIYUN_ACCESS_KEY_ID=test-id\nALIYUN_ACCESS_KEY_SECRET='test-secret'\n", encoding="utf-8")
    monkeypatch.delenv("ALIYUN_ACCESS_KEY_ID", raising=False)
    monkeypatch.setattr("app.storage.aliyun_oss._dotenv_candidates", lambda: [dotenv])

    assert _read_env_secret("ALIYUN_ACCESS_KEY_ID") == "test-id"
    assert _read_env_secret("ALIYUN_ACCESS_KEY_SECRET") == "test-secret"


def test_aliyun_provider_requires_bucket(monkeypatch):
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "secret")

    try:
        AliyunOssStorageProvider(bucket="", endpoint="https://oss-cn-hangzhou.aliyuncs.com")
    except StorageConfigurationError as exc:
        assert "bucket" in str(exc)
    else:
        raise AssertionError("Expected missing bucket to raise StorageConfigurationError")


def test_aliyun_provider_creates_presigned_download_url(monkeypatch):
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "secret")

    provider = AliyunOssStorageProvider(
        bucket="aimemo-dev",
        endpoint="https://oss-cn-hangzhou.aliyuncs.com",
    )

    signed = provider.create_download_url("users/u1/sync/manifest.json", expires_seconds=60)

    assert signed.method == "GET"
    assert signed.url.startswith("https://aimemo-dev.oss-cn-hangzhou.aliyuncs.com/users/u1/sync/manifest.json?")
    assert "OSSAccessKeyId=id" in signed.url
    assert "Signature=" in signed.url
