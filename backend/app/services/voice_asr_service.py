from __future__ import annotations

import logging

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings
from app.providers.dashscope_voice import DashScopeVoiceError, DashScopeVoiceProvider
from app.schemas.voice import VoiceTranscribeResponse


logger = logging.getLogger(__name__)

SUPPORTED_AUDIO_TYPES = {
    "audio/webm",
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
    "audio/ogg",
    "audio/opus",
    "audio/mpeg",
    "audio/mp3",
}


async def transcribe_audio_file(file: UploadFile, language: str | None = None) -> VoiceTranscribeResponse:
    if not settings.voice_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "VOICE_DISABLED", "message": "Voice module is disabled."},
        )

    raw_media_type = (file.content_type or "application/octet-stream").lower()
    media_type = raw_media_type.split(";", 1)[0].strip()
    if media_type not in SUPPORTED_AUDIO_TYPES:
        logger.warning("voice_asr_unsupported_media_type raw=%s normalized=%s", raw_media_type, media_type)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VOICE_UNSUPPORTED_AUDIO_FORMAT", "message": f"Unsupported audio type: {raw_media_type}"},
        )

    audio_bytes = await file.read()
    max_bytes = settings.voice_max_audio_mb * 1024 * 1024
    if len(audio_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "VOICE_AUDIO_TOO_LARGE", "message": "Audio file is too large."},
        )
    if not audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VOICE_EMPTY_AUDIO", "message": "Audio file is empty."},
        )

    try:
        text = DashScopeVoiceProvider().transcribe_audio(
            audio_bytes=audio_bytes,
            media_type=media_type,
            language=language or settings.voice_language,
        )
    except DashScopeVoiceError as exc:
        logger.warning(
            "voice_asr_failed code=%s media_type=%s bytes=%s message=%s",
            exc.code,
            media_type,
            len(audio_bytes),
            exc.message,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    if not text.strip():
        logger.warning("voice_asr_empty media_type=%s bytes=%s", media_type, len(audio_bytes))

    return VoiceTranscribeResponse(
        text=text,
        language=language or settings.voice_language,
        duration_ms=None,
        provider=settings.voice_asr_provider,
        model=settings.voice_aliyun_asr_model,
    )
