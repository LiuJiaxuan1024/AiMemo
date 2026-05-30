from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import settings


class DashScopeVoiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class SynthesizedAudio:
    content: bytes
    media_type: str


class DashScopeVoiceProvider:
    """Small HTTP adapter for DashScope voice endpoints.

    The provider keeps transport details out of service code. It accepts a few
    known response shapes because DashScope voice APIs differ between realtime,
    non-realtime, and customization endpoints.
    """

    def transcribe_audio(
        self,
        *,
        audio_bytes: bytes,
        media_type: str,
        language: str,
    ) -> str:
        payload = {
            "model": settings.voice_aliyun_asr_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": _to_data_url(audio_bytes, media_type),
                            },
                        }
                    ],
                }
            ],
            "stream": False,
        }
        if language and language.lower() not in {"auto", "detect"}:
            payload["asr_options"] = {"language": language}
        response = self._post_compatible_json("/chat/completions", payload)
        text = _extract_chat_completion_content(response) or _find_first_string(
            response,
            ("text", "transcript", "sentence", "content"),
        )
        if not text:
            raise DashScopeVoiceError("VOICE_ASR_FAILED", "ASR response did not contain recognized text.")
        return text

    def synthesize_speech(
        self,
        *,
        text: str,
        voice_id: str,
        model: str = "",
        voice_prompt: str,
        style_prompt: str,
        instruction: str,
    ) -> SynthesizedAudio:
        payload: dict[str, Any] = {
            "model": model or settings.voice_aliyun_tts_model,
            "input": {
                "text": text,
                "voice": voice_id or "Cherry",
            },
            "parameters": {
                "voice_prompt": voice_prompt or None,
                "style_prompt": style_prompt or None,
                "instruction": instruction or None,
                "sample_rate": settings.voice_aliyun_sample_rate,
            },
        }
        payload["parameters"] = {key: value for key, value in payload["parameters"].items() if value not in {None, ""}}
        response = self._post_json("/api/v1/services/aigc/multimodal-generation/generation", payload)
        audio = _extract_audio(response)
        if audio is None:
            raise DashScopeVoiceError("VOICE_TTS_FAILED", "TTS response did not contain playable audio.")
        return audio

    def design_voice(
        self,
        *,
        voice_prompt: str,
        target_model: str,
        name: str,
        preview_text: str = "",
        language: str = "zh",
    ) -> str:
        payload = {
            "model": settings.voice_aliyun_voice_design_model,
            "input": {
                "action": "create",
                "target_model": target_model,
                "preferred_name": _preferred_voice_name(name),
                "voice_prompt": voice_prompt,
                "preview_text": preview_text or None,
                "language": language or None,
            },
            "parameters": {
                "sample_rate": settings.voice_aliyun_sample_rate,
                "response_format": "wav",
            },
        }
        payload["input"] = {key: value for key, value in payload["input"].items() if value not in {None, ""}}
        response = self._post_json("/api/v1/services/audio/tts/customization", payload)
        voice_id = _find_first_string(response, ("voice", "voice_id", "id"))
        if not voice_id:
            raise DashScopeVoiceError("VOICE_DESIGN_FAILED", "Voice design response did not contain voice_id.")
        return voice_id

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_json_to_base(settings.voice_aliyun_base_url, path, payload)

    def _post_compatible_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_json_to_base(settings.dashscope_base_url, path, payload)

    def _post_json_to_base(self, base_url: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not settings.dashscope_api_key:
            raise DashScopeVoiceError("DASHSCOPE_API_KEY_MISSING", "DASHSCOPE_API_KEY is not configured.")

        url = f"{base_url.rstrip('/')}{path}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {settings.dashscope_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=settings.voice_aliyun_timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise DashScopeVoiceError("DASHSCOPE_REQUEST_FAILED", detail or str(exc)) from exc
        except URLError as exc:
            raise DashScopeVoiceError("DASHSCOPE_REQUEST_FAILED", str(exc.reason)) from exc
        except TimeoutError as exc:
            raise DashScopeVoiceError("DASHSCOPE_REQUEST_TIMEOUT", "DashScope voice request timed out.") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DashScopeVoiceError("DASHSCOPE_BAD_RESPONSE", "DashScope response was not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise DashScopeVoiceError("DASHSCOPE_BAD_RESPONSE", "DashScope response was not a JSON object.")
        return parsed


def _to_data_url(audio_bytes: bytes, media_type: str) -> str:
    encoded = base64.b64encode(audio_bytes).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _audio_format(media_type: str) -> str:
    lowered = media_type.lower()
    if "wav" in lowered:
        return "wav"
    if "ogg" in lowered or "opus" in lowered:
        return "ogg"
    if "mpeg" in lowered or "mp3" in lowered:
        return "mp3"
    return "webm"


def _preferred_voice_name(name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip()).strip("_").lower()
    if not normalized:
        normalized = "memo_elf_voice"
    if not normalized[0].isalpha():
        normalized = f"voice_{normalized}"
    return normalized[:64]


def _find_first_string(value: Any, keys: tuple[str, ...]) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for candidate in value.values():
            found = _find_first_string(candidate, keys)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_first_string(item, keys)
            if found:
                return found
    return ""


def _extract_chat_completion_content(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    choices = value.get("choices")
    if not isinstance(choices, list):
        return ""
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            found = _find_first_string(content, ("text", "transcript", "sentence", "content"))
            if found:
                return found
    return ""


def _extract_audio(value: Any) -> SynthesizedAudio | None:
    if isinstance(value, dict):
        for key in ("audio", "data", "content"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                audio = _decode_audio_string(candidate)
                if audio is not None:
                    return audio
        for key in ("url", "audio_url"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                return _download_audio(candidate)
        for candidate in value.values():
            audio = _extract_audio(candidate)
            if audio is not None:
                return audio
    if isinstance(value, list):
        for item in value:
            audio = _extract_audio(item)
            if audio is not None:
                return audio
    return None


def _decode_audio_string(value: str) -> SynthesizedAudio | None:
    if value.startswith("data:"):
        header, _, payload = value.partition(",")
        if not payload:
            return None
        media_type = header.removeprefix("data:").split(";")[0] or "audio/wav"
        return SynthesizedAudio(content=base64.b64decode(payload), media_type=media_type)
    try:
        content = base64.b64decode(value, validate=True)
    except Exception:
        return None
    if not content:
        return None
    return SynthesizedAudio(content=content, media_type="audio/wav")


def _download_audio(url: str) -> SynthesizedAudio:
    request = Request(url, headers={"Accept": "audio/*"})
    with urlopen(request, timeout=settings.voice_aliyun_timeout_seconds) as response:
        content = response.read()
        media_type = response.headers.get_content_type() or "audio/mpeg"
    return SynthesizedAudio(content=content, media_type=media_type)
