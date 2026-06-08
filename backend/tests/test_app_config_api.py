from fastapi.testclient import TestClient

from app.core.database import get_session
from app.main import create_app


def test_runtime_config_reads_current_project_config(monkeypatch, session_factory) -> None:
    monkeypatch.setattr("app.api.app_config.get_effective_runtime_config", lambda *args, **kwargs: False)
    app = create_app()

    def override_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)
    response = client.get("/api/config/runtime")

    assert response.status_code == 200
    assert response.json() == {"elf": {"enabled": False, "voice_mode_enabled": False}}
