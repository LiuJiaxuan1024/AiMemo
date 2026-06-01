from app.core.config import Settings


def test_resolved_cors_origins_include_runtime_dev_ports() -> None:
    settings = Settings(aimemo_host="127.0.0.1", aimemo_frontend_port=5174, aimemo_desktop_port=1421)

    origins = settings.resolved_cors_origins

    assert "http://127.0.0.1:5174" in origins
    assert "http://localhost:5174" in origins
    assert "http://127.0.0.1:1421" in origins
    assert "tauri://localhost" in origins
