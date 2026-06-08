from fastapi.testclient import TestClient

from app.core.database import get_session
from app.main import create_app
from app.providers.dashscope_voice import DashScopeVoiceProvider


def test_elf_voice_mode_defaults_off_and_can_toggle(session_factory) -> None:
    app = create_app()

    def override_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)

    off_response = client.put("/api/elf/voice/mode", json={"enabled": False})
    assert off_response.status_code == 200
    assert off_response.json() == {"enabled": False}

    get_response = client.get("/api/elf/voice/mode")
    assert get_response.status_code == 200
    assert get_response.json() == {"enabled": False}

    on_response = client.put("/api/elf/voice/mode", json={"enabled": True})
    assert on_response.status_code == 200
    assert on_response.json() == {"enabled": True}


def test_elf_voice_transcribe_accepts_webm_codec_content_type(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_transcribe_audio(self, *, audio_bytes: bytes, media_type: str, language: str) -> str:  # noqa: ARG001
        captured["media_type"] = media_type
        captured["language"] = language
        return "你好"

    monkeypatch.setattr(DashScopeVoiceProvider, "transcribe_audio", fake_transcribe_audio)
    client = TestClient(create_app())
    response = client.post(
        "/api/elf/voice/transcribe",
        files={"file": ("elf-voice.webm", b"fake-audio", "audio/webm;codecs=opus")},
    )

    assert response.status_code == 200
    assert response.json()["text"] == "你好"
    assert captured["media_type"] == "audio/webm"
