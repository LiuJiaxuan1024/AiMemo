from __future__ import annotations

import json

from fastapi import HTTPException, status
from sqlmodel import Session, desc, select

from app.core.config import settings
from app.models.note import utc_now
from app.models.voice_profile import VoiceProfile
from app.schemas.voice import VoiceProfileCreate, VoiceProfileRead, VoiceProfileUpdate


ALLOWED_SOURCE_TYPES = {"builtin", "designed", "cloned", "draft"}
ALLOWED_PROFILE_STATUSES = {"draft", "generating", "ready", "failed"}


def ensure_default_voice_profile(session: Session) -> VoiceProfile:
    existing = session.exec(select(VoiceProfile).where(VoiceProfile.is_active == True)).first()  # noqa: E712
    if existing is not None:
        return existing

    any_profile = session.exec(select(VoiceProfile).order_by(VoiceProfile.id).limit(1)).first()
    if any_profile is not None:
        any_profile.is_active = True
        any_profile.status = "ready"
        any_profile.updated_at = utc_now()
        session.add(any_profile)
        session.commit()
        session.refresh(any_profile)
        return any_profile

    profile = VoiceProfile(
        name="默认精灵声线",
        description="阿里云 Qwen3-TTS 默认实时声线。",
        preview_text="今天也一起把事情慢慢做好吧。",
        language=settings.voice_language,
        remote_provider=settings.voice_tts_provider,
        remote_model=settings.voice_aliyun_tts_model,
        remote_voice_id="Cherry",
        source_type="builtin",
        status="ready",
        is_active=True,
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


def list_voice_profiles(session: Session) -> list[VoiceProfileRead]:
    ensure_default_voice_profile(session)
    profiles = session.exec(
        select(VoiceProfile).order_by(desc(VoiceProfile.is_active), desc(VoiceProfile.updated_at), desc(VoiceProfile.id))
    ).all()
    return [_to_profile_read(profile) for profile in profiles]


def get_voice_profile(session: Session, profile_id: int) -> VoiceProfileRead:
    return _to_profile_read(_get_profile_or_404(session, profile_id))


def get_active_voice_profile(session: Session) -> VoiceProfile:
    return ensure_default_voice_profile(session)


def create_voice_profile(session: Session, payload: VoiceProfileCreate) -> VoiceProfileRead:
    profile = VoiceProfile(
        name=_normalize_text(payload.name, 120, "name"),
        description=(payload.description or "").strip()[:1000],
        voice_prompt=(payload.voice_prompt or "").strip()[:2048],
        style_prompt=(payload.style_prompt or "").strip()[:1000],
        preview_text=(payload.preview_text or "").strip()[:500],
        language=(payload.language or "auto").strip()[:24],
        speed=_normalize_range(payload.speed, 0.5, 2.0, "speed"),
        energy=_normalize_range(payload.energy, 0.0, 2.0, "energy"),
        emotion_bias=json.dumps(payload.emotion_bias or {}, ensure_ascii=False),
        remote_provider=(payload.remote_provider or settings.voice_tts_provider).strip()[:80],
        remote_model=(payload.remote_model or settings.voice_aliyun_tts_model).strip()[:120],
        remote_target_model=(payload.remote_target_model or "").strip()[:120],
        remote_voice_id=(payload.remote_voice_id or "").strip()[:200],
        source_type=_validate_source_type(payload.source_type),
        status=_validate_status(payload.status),
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return _to_profile_read(profile)


def update_voice_profile(
    session: Session,
    profile_id: int,
    payload: VoiceProfileUpdate,
) -> VoiceProfileRead:
    if not payload.model_fields_set:
        raise _bad_request("At least one field is required.")

    profile = _get_profile_or_404(session, profile_id)
    if "name" in payload.model_fields_set and payload.name is not None:
        profile.name = _normalize_text(payload.name, 120, "name")
    if "description" in payload.model_fields_set:
        profile.description = (payload.description or "").strip()[:1000]
    if "voice_prompt" in payload.model_fields_set:
        profile.voice_prompt = (payload.voice_prompt or "").strip()[:2048]
    if "style_prompt" in payload.model_fields_set:
        profile.style_prompt = (payload.style_prompt or "").strip()[:1000]
    if "preview_text" in payload.model_fields_set:
        profile.preview_text = (payload.preview_text or "").strip()[:500]
    if "language" in payload.model_fields_set and payload.language is not None:
        profile.language = payload.language.strip()[:24]
    if "speed" in payload.model_fields_set and payload.speed is not None:
        profile.speed = _normalize_range(payload.speed, 0.5, 2.0, "speed")
    if "energy" in payload.model_fields_set and payload.energy is not None:
        profile.energy = _normalize_range(payload.energy, 0.0, 2.0, "energy")
    if "emotion_bias" in payload.model_fields_set:
        profile.emotion_bias = json.dumps(payload.emotion_bias or {}, ensure_ascii=False)
    if "remote_voice_id" in payload.model_fields_set:
        profile.remote_voice_id = (payload.remote_voice_id or "").strip()[:200]
    if "status" in payload.model_fields_set and payload.status is not None:
        profile.status = _validate_status(payload.status)

    profile.updated_at = utc_now()
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return _to_profile_read(profile)


def activate_voice_profile(session: Session, profile_id: int) -> VoiceProfileRead:
    profile = _get_profile_or_404(session, profile_id)
    if profile.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "VOICE_PROFILE_NOT_READY", "message": "Only ready voice profiles can be activated."},
        )

    active_profiles = session.exec(select(VoiceProfile).where(VoiceProfile.is_active == True)).all()  # noqa: E712
    now = utc_now()
    for active in active_profiles:
        active.is_active = False
        active.updated_at = now
        session.add(active)
    profile.is_active = True
    profile.updated_at = now
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return _to_profile_read(profile)


def delete_voice_profile(session: Session, profile_id: int) -> None:
    profile = _get_profile_or_404(session, profile_id)
    if profile.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "VOICE_PROFILE_ACTIVE", "message": "Active voice profile cannot be deleted."},
        )
    session.delete(profile)
    session.commit()


def save_designed_profile(
    session: Session,
    *,
    name: str,
    description: str,
    voice_prompt: str,
    style_prompt: str,
    preview_text: str,
    remote_voice_id: str,
    status_value: str,
    last_error: str = "",
) -> VoiceProfile:
    profile = VoiceProfile(
        name=_normalize_text(name, 120, "name"),
        description=description.strip()[:1000],
        voice_prompt=voice_prompt.strip()[:2048],
        style_prompt=style_prompt.strip()[:1000],
        preview_text=preview_text.strip()[:500],
        language="zh",
        remote_provider=settings.voice_design_provider,
        remote_model=settings.voice_aliyun_voice_design_model,
        remote_target_model=settings.voice_aliyun_voice_design_target_model,
        remote_voice_id=remote_voice_id.strip()[:200],
        source_type="designed",
        status=_validate_status(status_value),
        last_error=last_error.strip()[:2000],
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


def _get_profile_or_404(session: Session, profile_id: int) -> VoiceProfile:
    profile = session.get(VoiceProfile, profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "VOICE_PROFILE_NOT_FOUND", "message": "Voice profile not found."},
        )
    return profile


def _to_profile_read(profile: VoiceProfile) -> VoiceProfileRead:
    return VoiceProfileRead(
        id=profile.id or 0,
        name=profile.name,
        description=profile.description,
        voice_prompt=profile.voice_prompt,
        style_prompt=profile.style_prompt,
        preview_text=profile.preview_text,
        language=profile.language,
        speed=profile.speed,
        energy=profile.energy,
        emotion_bias=_parse_json_object(profile.emotion_bias),
        remote_provider=profile.remote_provider,
        remote_model=profile.remote_model,
        remote_target_model=profile.remote_target_model,
        remote_voice_id=profile.remote_voice_id,
        source_type=profile.source_type,
        status=profile.status,
        last_error=profile.last_error,
        is_active=profile.is_active,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _parse_json_object(value: str) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_text(value: str, max_length: int, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise _bad_request(f"{field_name} cannot be empty.")
    return normalized[:max_length]


def _normalize_range(value: float, minimum: float, maximum: float, field_name: str) -> float:
    if value < minimum or value > maximum:
        raise _bad_request(f"{field_name} must be between {minimum} and {maximum}.")
    return float(value)


def _validate_source_type(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in ALLOWED_SOURCE_TYPES:
        raise _bad_request("Invalid voice profile source_type.")
    return normalized


def _validate_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in ALLOWED_PROFILE_STATUSES:
        raise _bad_request("Invalid voice profile status.")
    return normalized


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
