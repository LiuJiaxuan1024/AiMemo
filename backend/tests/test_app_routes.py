import mimetypes

from app.main import create_app


def test_app_registers_search_routes():
    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/api/search/notes" in paths
    assert "/api/conversations" in paths
    assert "/api/conversations/{conversation_id}/messages" in paths
    assert "/api/conversations/{conversation_id}/export" in paths
    assert "/api/conversations/{conversation_id}/export/snapshot" in paths
    assert "/api/conversations/{conversation_id}/chat" in paths
    delete_route = next(
        (
            route
            for route in app.routes
            if getattr(route, "path", "") == "/api/conversations/{conversation_id}"
            and "DELETE" in getattr(route, "methods", set())
        ),
        None,
    )
    assert delete_route is not None
    assert "/api/memories" in paths
    assert "/api/memories/{memory_id}" in paths
    assert "/api/knowledge/spaces" in paths
    assert "/api/knowledge/search" in paths
    assert "/api/knowledge/spaces/{space_id}/documents" in paths
    assert "/api/knowledge/spaces/{space_id}/documents/upload" in paths
    assert "/api/knowledge/documents/{document_id}/chunk-drafts" in paths
    assert "/api/conversations/{conversation_id}/knowledge-mounts" in paths
    assert "/api/background_tasks" in paths
    assert "/api/background_tasks/{task_id}" in paths
    assert "/api/background_tasks/{task_id}/output" in paths
    assert "/api/background_tasks/{task_id}/kill" in paths
    assert "/api/cloud-sync/status" in paths
    assert "/api/cloud-sync/pull" in paths
    assert "/api/cloud-sync/push" in paths
    assert "/api/cloud-sync/sync" in paths
    assert "/api/cloud-sync/repairs/conversation-attachment-paths" in paths
    assert "/api/note-categories" in paths
    assert "/api/note-categories/{category_id}" in paths
    assert "/api/note-tags" in paths
    assert "/api/note-tags/rename" in paths
    assert "/api/note-tags/merge" in paths
    assert "/api/note-tags/delete" in paths


def test_frontend_static_module_mime_types_are_registered():
    create_app()

    assert mimetypes.guess_type("index-test.js")[0] == "text/javascript"
    assert mimetypes.guess_type("chunk-test.mjs")[0] == "text/javascript"
    assert mimetypes.guess_type("style-test.css")[0] == "text/css"
    assert mimetypes.guess_type("module-test.wasm")[0] == "application/wasm"
