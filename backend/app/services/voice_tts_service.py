from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlmodel import Session

from app.core.config import settings
from app.models.voice_profile import VoiceProfile
from app.providers.dashscope_voice import DashScopeVoiceError, DashScopeVoiceProvider, SynthesizedAudio
from app.services.voice_profile_service import get_active_voice_profile


logger = logging.getLogger(__name__)

EMOJI_STYLE_MODIFIERS = {
    "soft": "gentle, warm, close",
    "happy": "cheerful, bright, smiling tone",
    "shy": "shy, soft, slightly nervous",
    "angry": "cute tsundere, mildly annoyed, not aggressive",
    "thinking": "thoughtful, slower, quiet",
    "worried": "concerned, careful, soft",
    "sleepy": "sleepy, low energy",
    "curious": "curious, lively",
    "success": "pleased, warm, quietly excited",
    "error": "apologetic, careful",
}


def synthesize_bubble_voice(
    session: Session,
    *,
    text: str,
    emoji: str | None = None,
    profile_id: int | None = None,
) -> SynthesizedAudio:
    _ensure_voice_enabled()
    profile = session.get(VoiceProfile, profile_id) if profile_id is not None else get_active_voice_profile(session)
    if profile is None:
        logger.warning("voice_tts_profile_missing profile_id=%s", profile_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "VOICE_PROFILE_NOT_FOUND", "message": "Voice profile not found."},
        )
    if profile.status != "ready":
        logger.warning(
            "voice_tts_profile_not_ready profile_id=%s status=%s remote_voice_id=%s",
            profile.id,
            profile.status,
            profile.remote_voice_id,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "VOICE_PROFILE_NOT_READY", "message": "Voice profile is not ready."},
        )
    instruction = _build_instruction(profile, emoji)
    try:
        return DashScopeVoiceProvider().synthesize_speech(
            text=text.strip(),
            voice_id=profile.remote_voice_id,
            model=_tts_model_for_profile(profile),
            voice_prompt=profile.voice_prompt,
            style_prompt=profile.style_prompt,
            instruction=instruction,
        )
    except DashScopeVoiceError as exc:
        logger.warning(
            "voice_tts_failed code=%s profile_id=%s remote_voice_id=%s model=%s text_chars=%s message=%s",
            exc.code,
            profile.id,
            profile.remote_voice_id,
            _tts_model_for_profile(profile),
            len(text),
            exc.message,
        )
        raise _provider_http_error(exc) from exc


def _build_instruction(profile: VoiceProfile, emoji: str | None) -> str:
    parts = []
    if profile.style_prompt:
        parts.append(profile.style_prompt)
    modifier = EMOJI_STYLE_MODIFIERS.get((emoji or "").strip().lower())
    if modifier:
        parts.append(modifier)
    if profile.speed != 1.0:
        parts.append(f"speed multiplier {profile.speed:.2f}")
    if profile.energy != 1.0:
        parts.append(f"energy multiplier {profile.energy:.2f}")
    return "; ".join(parts)


def _tts_model_for_profile(profile: VoiceProfile) -> str:
    if profile.remote_target_model:
        return profile.remote_target_model
    if profile.remote_model:
        return profile.remote_model
    return settings.voice_aliyun_tts_model


def _ensure_voice_enabled() -> None:
    if settings.voice_enabled:
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "VOICE_DISABLED", "message": "Voice module is disabled."},
    )


def _provider_http_error(exc: DashScopeVoiceError) -> HTTPException:
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": exc.message},
    )
