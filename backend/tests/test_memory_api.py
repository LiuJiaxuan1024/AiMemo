from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.database import get_session
from app.main import create_app
from app.models.long_term_memory import LongTermMemory
from app.services.memory_service import build_memory_content_hash


def test_memories_api_lists_updates_disables_and_reactivates_memory(session):
    memory = _add_memory(session, "用户不吃香菜。")
    app = create_app()

    def override_get_session() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    list_response = client.get("/api/memories")
    assert list_response.status_code == 200
    assert list_response.json()[0]["content"] == "用户不吃香菜。"

    patch_response = client.patch(
        f"/api/memories/{memory.id}",
        json={
            "content": "用户不吃香菜，也不喜欢葱。",
            "category": "preference",
            "importance": 0.95,
        },
    )
    assert patch_response.status_code == 200
    patched = patch_response.json()
    assert patched["content"] == "用户不吃香菜，也不喜欢葱。"
    assert patched["importance"] == 0.95
    assert patched["content_hash"] == build_memory_content_hash(
        "preference",
        "用户不吃香菜，也不喜欢葱。",
    )

    delete_response = client.delete(f"/api/memories/{memory.id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "archived"

    default_list_response = client.get("/api/memories")
    assert default_list_response.status_code == 200
    assert default_list_response.json() == []

    archived_list_response = client.get("/api/memories?status=archived")
    assert archived_list_response.status_code == 200
    assert archived_list_response.json()[0]["id"] == memory.id

    activate_response = client.patch(
        f"/api/memories/{memory.id}",
        json={"status": "active"},
    )
    assert activate_response.status_code == 200
    assert activate_response.json()["status"] == "active"

    active_list_response = client.get("/api/memories")
    assert active_list_response.status_code == 200
    assert active_list_response.json()[0]["id"] == memory.id


def test_memories_api_deletes_only_disabled_memory(session):
    memory = _add_memory(session, "用户喜欢安静的工作环境。")
    app = create_app()

    def override_get_session() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    active_delete_response = client.delete(f"/api/memories/{memory.id}/hard")
    assert active_delete_response.status_code == 400

    archive_response = client.delete(f"/api/memories/{memory.id}")
    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "archived"

    hard_delete_response = client.delete(f"/api/memories/{memory.id}/hard")
    assert hard_delete_response.status_code == 204
    assert hard_delete_response.content == b""

    get_response = client.get(f"/api/memories/{memory.id}")
    assert get_response.status_code == 404


def _add_memory(session, content: str) -> LongTermMemory:
    memory = LongTermMemory(
        level=4,
        category="preference",
        content=content,
        summary=content[:20],
        importance=0.9,
        confidence=0.9,
        status="active",
        content_hash=build_memory_content_hash("preference", content),
    )
    session.add(memory)
    session.commit()
    session.refresh(memory)
    return memory
