from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.core.database import get_session
from app.main import create_app
from app.schemas.note import NoteCreate
from app.services.note_service import create_note
from app.storage.local_mock import LocalMockStorageProvider


def test_cloud_sync_status_and_push_api(session_factory, tmp_path, monkeypatch):
    provider = LocalMockStorageProvider(tmp_path)
    monkeypatch.setattr(settings, "storage_provider", "local_mock")
    monkeypatch.setattr(settings, "storage_sync_user_id", "api-user")
    monkeypatch.setattr("app.services.cloud_sync_service.get_storage_provider", lambda: provider)

    with session_factory() as session:
        create_note(session, NoteCreate(content="API 同步测试"))

    app = create_app()

    def override_get_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    status_before = client.get("/api/cloud-sync/status")
    push_response = client.post("/api/cloud-sync/push")
    status_after = client.get("/api/cloud-sync/status")

    assert status_before.status_code == 200
    assert status_before.json()["dirty_note_count"] == 1
    assert status_before.json()["provider"] == "local_mock"
    assert push_response.status_code == 200
    assert push_response.json()["uploaded_note_count"] == 1
    assert status_after.status_code == 200
    assert status_after.json()["dirty_note_count"] == 0
