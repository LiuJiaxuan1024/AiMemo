from fastapi.testclient import TestClient

from app.main import create_app


def test_runtime_config_reads_current_project_config(monkeypatch) -> None:
    monkeypatch.setattr("app.api.app_config.get_project_config_value", lambda *args, **kwargs: False)
    client = TestClient(create_app())

    response = client.get("/api/config/runtime")

    assert response.status_code == 200
    assert response.json() == {"elf": {"enabled": False}}
