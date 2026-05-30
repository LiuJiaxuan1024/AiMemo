import json

import pytest

from app.providers.dashscope_voice import DashScopeVoiceProvider


def test_design_voice_uses_dashscope_customization_create_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post_json(self: DashScopeVoiceProvider, path: str, payload: dict) -> dict:  # noqa: ARG001
        captured["path"] = path
        captured["payload"] = json.loads(json.dumps(payload))
        return {"output": {"voice": "voice_memo_elf"}}

    monkeypatch.setattr(DashScopeVoiceProvider, "_post_json", fake_post_json)

    voice_id = DashScopeVoiceProvider().design_voice(
        voice_prompt="温暖、清澈的桌面精灵声线",
        target_model="qwen3-tts-vd-2026-01-26",
        name="暖糖",
        preview_text="今天也一起把事情做好吧。",
        language="zh",
    )

    assert voice_id == "voice_memo_elf"
    assert captured["path"] == "/api/v1/services/audio/tts/customization"
    payload = captured["payload"]
    assert payload["model"] == "qwen-voice-design"
    assert payload["input"]["action"] == "create"
    assert payload["input"]["target_model"] == "qwen3-tts-vd-2026-01-26"
    assert payload["input"]["preferred_name"] == "memo_elf_voice"
    assert payload["input"]["voice_prompt"] == "温暖、清澈的桌面精灵声线"
    assert payload["input"]["preview_text"] == "今天也一起把事情做好吧。"
    assert payload["input"]["language"] == "zh"


def test_synthesize_speech_can_override_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post_json(self: DashScopeVoiceProvider, path: str, payload: dict) -> dict:  # noqa: ARG001
        captured["path"] = path
        captured["payload"] = json.loads(json.dumps(payload))
        return {"output": {"audio": "UklGRg=="}}

    monkeypatch.setattr(DashScopeVoiceProvider, "_post_json", fake_post_json)

    audio = DashScopeVoiceProvider().synthesize_speech(
        text="试听一下。",
        voice_id="qwen-tts-vd-memo_elf-voice",
        model="qwen3-tts-vd-2026-01-26",
        voice_prompt="温暖清澈",
        style_prompt="自然",
        instruction="gentle",
    )

    assert audio.media_type == "audio/wav"
    payload = captured["payload"]
    assert captured["path"] == "/api/v1/services/aigc/multimodal-generation/generation"
    assert payload["model"] == "qwen3-tts-vd-2026-01-26"
    assert payload["input"]["voice"] == "qwen-tts-vd-memo_elf-voice"


def test_transcribe_audio_uses_compatible_chat_completions(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post_compatible_json(self: DashScopeVoiceProvider, path: str, payload: dict) -> dict:  # noqa: ARG001
        captured["path"] = path
        captured["payload"] = json.loads(json.dumps(payload))
        return {"choices": [{"message": {"content": "你好，精灵。"}}]}

    monkeypatch.setattr(DashScopeVoiceProvider, "_post_compatible_json", fake_post_compatible_json)

    text = DashScopeVoiceProvider().transcribe_audio(
        audio_bytes=b"fake-audio",
        media_type="audio/webm",
        language="auto",
    )

    assert text == "你好，精灵。"
    assert captured["path"] == "/chat/completions"
    payload = captured["payload"]
    assert payload["model"] == "qwen3-asr-flash"
    assert payload["messages"][0]["content"][0]["type"] == "input_audio"
    assert payload["messages"][0]["content"][0]["input_audio"]["data"].startswith("data:audio/webm;base64,")
