from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import Response as FastAPIResponse
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.voice import (
    VoiceDesignRequest,
    VoiceDesignResponse,
    VoicePreviewRequest,
    VoiceProfileCreate,
    VoiceProfileRead,
    VoiceProfileUpdate,
)
from app.services.voice_design_service import design_voice_profile
from app.services.voice_profile_service import (
    activate_voice_profile,
    create_voice_profile,
    delete_voice_profile,
    get_voice_profile,
    list_voice_profiles,
    update_voice_profile,
)
from app.services.voice_tts_service import synthesize_bubble_voice


router = APIRouter(prefix="/voice/profiles", tags=["voice_profiles"])


@router.get("", response_model=list[VoiceProfileRead])
def list_voice_profiles_api(session: Session = Depends(get_session)) -> list[VoiceProfileRead]:
    return list_voice_profiles(session)


@router.post("", response_model=VoiceProfileRead, status_code=status.HTTP_201_CREATED)
def create_voice_profile_api(
    payload: VoiceProfileCreate,
    session: Session = Depends(get_session),
) -> VoiceProfileRead:
    return create_voice_profile(session, payload)


@router.post("/design", response_model=VoiceDesignResponse)
def design_voice_profile_api(
    payload: VoiceDesignRequest,
    session: Session = Depends(get_session),
) -> VoiceDesignResponse:
    return design_voice_profile(session, payload)


@router.get("/{profile_id}", response_model=VoiceProfileRead)
def get_voice_profile_api(
    profile_id: int,
    session: Session = Depends(get_session),
) -> VoiceProfileRead:
    return get_voice_profile(session, profile_id)


@router.patch("/{profile_id}", response_model=VoiceProfileRead)
def update_voice_profile_api(
    profile_id: int,
    payload: VoiceProfileUpdate,
    session: Session = Depends(get_session),
) -> VoiceProfileRead:
    return update_voice_profile(session, profile_id, payload)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_voice_profile_api(
    profile_id: int,
    session: Session = Depends(get_session),
) -> Response:
    delete_voice_profile(session, profile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{profile_id}/activate", response_model=VoiceProfileRead)
def activate_voice_profile_api(
    profile_id: int,
    session: Session = Depends(get_session),
) -> VoiceProfileRead:
    return activate_voice_profile(session, profile_id)


@router.post("/{profile_id}/preview")
def preview_voice_profile_api(
    profile_id: int,
    payload: VoicePreviewRequest,
    session: Session = Depends(get_session),
) -> FastAPIResponse:
    profile = get_voice_profile(session, profile_id)
    text = (payload.text or profile.preview_text or "今天也一起把事情慢慢做好吧。").strip()
    audio = synthesize_bubble_voice(session, text=text, emoji=payload.emoji, profile_id=profile_id)
    return FastAPIResponse(
        content=audio.content,
        media_type=audio.media_type,
        headers={"Cache-Control": "no-store"},
    )

