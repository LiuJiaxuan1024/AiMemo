import pytest
from fastapi.testclient import TestClient

from app.core.config import get_project_config_value, set_project_config_value
from app.core.database import get_session
from app.main import create_app


def test_runtime_config_reports_voice_mode(monkeypatch, session_factory) -> None:
    monkeypatch.setattr(
        "app.services.runtime_config_service.get_project_config_value",
        lambda path, default, *, reload=False: default,
    )
    app = create_app()

    def override_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)
    response = client.get("/api/config/runtime")

    assert response.status_code == 200
    assert response.json() == {"elf": {"voice_mode_enabled": False}}


def test_project_config_writer_persists_voice_settings(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json5"
    config_path.write_text(
        """{
  // comment should survive targeted updates
  "elf": {
    "enabled": true,
  },
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.core.config._config_candidates", lambda: [config_path])
    monkeypatch.setattr("app.core.config._PROJECT_CONFIG", None)

    set_project_config_value("elf.voice.mode", True)
    set_project_config_value("elf.voice.default_profile_id", 7)

    text = config_path.read_text(encoding="utf-8")
    assert "// comment should survive targeted updates" in text
    assert get_project_config_value("elf.enabled", False, reload=True) is True
    assert get_project_config_value("elf.voice.mode", False, reload=True) is True
    assert get_project_config_value("elf.voice.default_profile_id", None, reload=True) == 7


def test_project_config_writer_creates_voice_section(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json5"
    config_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("app.core.config._config_candidates", lambda: [config_path])
    monkeypatch.setattr("app.core.config._PROJECT_CONFIG", None)

    set_project_config_value("elf.voice.mode", True)

    assert get_project_config_value("elf.voice.mode", False, reload=True) is True


def test_project_config_writer_rejects_elf_enabled(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json5"
    config_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("app.core.config._config_candidates", lambda: [config_path])
    monkeypatch.setattr("app.core.config._PROJECT_CONFIG", None)

    with pytest.raises(ValueError, match="elf.enabled"):
        set_project_config_value("elf.enabled", False)
