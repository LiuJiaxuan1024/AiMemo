from app.storage.local_mock import LocalMockStorageProvider
from app.storage.provider import StorageNotFoundError


def test_local_mock_storage_provider_put_get_head_list_delete(tmp_path):
    provider = LocalMockStorageProvider(tmp_path)

    written = provider.put_bytes(
        "users/u1/sync/manifest.json",
        b'{"ok": true}',
        content_type="application/json",
    )

    assert written.key == "users/u1/sync/manifest.json"
    assert written.size_bytes == len(b'{"ok": true}')
    assert provider.get_bytes("users/u1/sync/manifest.json") == b'{"ok": true}'
    assert provider.head_object("users/u1/sync/manifest.json") is not None
    assert [item.key for item in provider.list_objects("users/u1/sync")] == [
        "users/u1/sync/manifest.json"
    ]

    provider.delete_object("users/u1/sync/manifest.json")

    assert provider.head_object("users/u1/sync/manifest.json") is None
    try:
        provider.get_bytes("users/u1/sync/manifest.json")
    except StorageNotFoundError:
        pass
    else:
        raise AssertionError("Expected missing object to raise StorageNotFoundError")
