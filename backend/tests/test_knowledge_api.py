from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.database import get_session
from app.main import create_app


def _client(session: Session) -> TestClient:
    app = create_app()

    def override_get_session() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def test_knowledge_space_crud_and_archive(session: Session) -> None:
    client = _client(session)

    create_response = client.post(
        "/api/knowledge/spaces",
        json={"name": " Zenoh 项目资料 ", "description": "迁移材料", "icon": "sparkles"},
    )
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["name"] == "Zenoh 项目资料"
    assert created["status"] == "active"
    assert created["document_count"] == 0
    assert created["ready_document_count"] == 0

    list_response = client.get("/api/knowledge/spaces")
    assert list_response.status_code == 200
    assert [space["id"] for space in list_response.json()] == [created["id"]]

    patch_response = client.patch(
        f"/api/knowledge/spaces/{created['id']}",
        json={"description": "Zenoh 与 C++ 迁移资料"},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["description"] == "Zenoh 与 C++ 迁移资料"

    archive_response = client.delete(f"/api/knowledge/spaces/{created['id']}")
    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "archived"

    active_list_response = client.get("/api/knowledge/spaces")
    assert active_list_response.status_code == 200
    assert active_list_response.json() == []

    archived_list_response = client.get("/api/knowledge/spaces?include_archived=true")
    assert archived_list_response.status_code == 200
    assert archived_list_response.json()[0]["id"] == created["id"]


def test_conversation_mount_crud_requires_existing_conversation_and_active_space(session: Session) -> None:
    client = _client(session)

    space_response = client.post("/api/knowledge/spaces", json={"name": "技术资料"})
    assert space_response.status_code == 201
    space_id = space_response.json()["id"]

    missing_conversation_response = client.post(f"/api/conversations/999/knowledge-mounts/{space_id}")
    assert missing_conversation_response.status_code == 404

    conversation_response = client.post("/api/conversations", json={"title": "测试对话"})
    assert conversation_response.status_code == 201
    conversation_id = conversation_response.json()["id"]

    add_response = client.post(f"/api/conversations/{conversation_id}/knowledge-mounts/{space_id}")
    assert add_response.status_code == 200
    assert add_response.json()["space_id"] == space_id
    assert add_response.json()["space_name"] == "技术资料"

    duplicate_response = client.post(f"/api/conversations/{conversation_id}/knowledge-mounts/{space_id}")
    assert duplicate_response.status_code == 200
    list_response = client.get(f"/api/conversations/{conversation_id}/knowledge-mounts")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    archived_space_response = client.post("/api/knowledge/spaces", json={"name": "旧资料"})
    archived_space_id = archived_space_response.json()["id"]
    archive_response = client.delete(f"/api/knowledge/spaces/{archived_space_id}")
    assert archive_response.status_code == 200

    archived_mount_response = client.post(
        f"/api/conversations/{conversation_id}/knowledge-mounts/{archived_space_id}"
    )
    assert archived_mount_response.status_code == 409

    delete_response = client.delete(f"/api/conversations/{conversation_id}/knowledge-mounts/{space_id}")
    assert delete_response.status_code == 204
    assert client.get(f"/api/conversations/{conversation_id}/knowledge-mounts").json() == []


def test_replace_mounts_validates_active_spaces_and_archive_removes_mounts(session: Session) -> None:
    client = _client(session)

    conversation_id = client.post("/api/conversations", json={"title": "RAG"}).json()["id"]
    first_space_id = client.post("/api/knowledge/spaces", json={"name": "项目 A"}).json()["id"]
    second_space_id = client.post("/api/knowledge/spaces", json={"name": "项目 B"}).json()["id"]
    archived_space_id = client.post("/api/knowledge/spaces", json={"name": "旧项目"}).json()["id"]
    client.delete(f"/api/knowledge/spaces/{archived_space_id}")

    replace_response = client.put(
        f"/api/conversations/{conversation_id}/knowledge-mounts",
        json={"space_ids": [first_space_id, second_space_id, first_space_id]},
    )
    assert replace_response.status_code == 200
    assert [mount["space_id"] for mount in replace_response.json()] == [first_space_id, second_space_id]

    invalid_replace_response = client.put(
        f"/api/conversations/{conversation_id}/knowledge-mounts",
        json={"space_ids": [archived_space_id]},
    )
    assert invalid_replace_response.status_code == 409

    archive_response = client.delete(f"/api/knowledge/spaces/{first_space_id}")
    assert archive_response.status_code == 200

    mounts_after_archive = client.get(f"/api/conversations/{conversation_id}/knowledge-mounts").json()
    assert [mount["space_id"] for mount in mounts_after_archive] == [second_space_id]


def test_documents_and_chunks_endpoints_are_available_before_ingest(session: Session) -> None:
    client = _client(session)
    space_id = client.post("/api/knowledge/spaces", json={"name": "空知库"}).json()["id"]

    documents_response = client.get(f"/api/knowledge/spaces/{space_id}/documents")
    assert documents_response.status_code == 200
    assert documents_response.json() == []

    missing_document_response = client.get("/api/knowledge/documents/999")
    assert missing_document_response.status_code == 404

    missing_chunks_response = client.get("/api/knowledge/documents/999/chunks")
    assert missing_chunks_response.status_code == 404


def test_upload_document_stores_file_and_previews_chunk_drafts(session: Session, tmp_path, monkeypatch) -> None:
    from app.services import knowledge_document_service

    monkeypatch.setattr(knowledge_document_service, "KNOWLEDGE_DATA_ROOT", tmp_path / "knowledge")
    client = _client(session)
    space_id = client.post("/api/knowledge/spaces", json={"name": "上传测试"}).json()["id"]

    response = client.post(
        f"/api/knowledge/spaces/{space_id}/documents/upload",
        files={"file": ("guide.md", b"# Title\n\nBody text.", "text/markdown")},
        data={"title": "自定义标题"},
    )

    assert response.status_code == 201
    payload = response.json()
    document = payload["document"]
    assert payload["job"]["type"] == "knowledge_ingest"
    assert payload["job"]["graph_name"] == "knowledge_ingest_graph"
    assert payload["job"]["status"] == "pending"
    assert document["space_id"] == space_id
    assert document["title"] == "自定义标题"
    assert document["original_filename"] == "guide.md"
    assert document["parser"] == "markdown"
    assert document["status"] == "pending"
    assert document["storage_path"].startswith(f"files/{space_id}/{document['id']}/")

    list_response = client.get(f"/api/knowledge/spaces/{space_id}/documents")
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [document["id"]]

    drafts_response = client.get(f"/api/knowledge/documents/{document['id']}/chunk-drafts")
    assert drafts_response.status_code == 200
    drafts = drafts_response.json()
    assert drafts[0]["heading_path"] == ["Title"]
    assert "Body text" in drafts[-1]["text"]
