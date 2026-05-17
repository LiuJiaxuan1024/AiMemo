from app.main import create_app


def test_app_registers_search_routes():
    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/api/search/notes" in paths
    assert "/api/conversations" in paths
    assert "/api/conversations/{conversation_id}/messages" in paths
    assert "/api/conversations/{conversation_id}/chat" in paths
    assert "/api/memories" in paths
    assert "/api/memories/{memory_id}" in paths
