from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.voice import ElfVoiceModeRead, ElfVoiceModeUpdate, VoiceSpeakRequest, VoiceTranscribeResponse
from app.services.voice_asr_service import transcribe_audio_file
from app.services.voice_tts_service import synthesize_bubble_voice


router = APIRouter(prefix="/elf/voice", tags=["elf_voice"])
_elf_voice_mode_enabled = False


@router.get("/mode", response_model=ElfVoiceModeRead)
def get_elf_voice_mode_api() -> ElfVoiceModeRead:
    return ElfVoiceModeRead(enabled=_elf_voice_mode_enabled)


@router.put("/mode", response_model=ElfVoiceModeRead)
def update_elf_voice_mode_api(payload: ElfVoiceModeUpdate) -> ElfVoiceModeRead:
    global _elf_voice_mode_enabled
    _elf_voice_mode_enabled = payload.enabled
    return ElfVoiceModeRead(enabled=_elf_voice_mode_enabled)


@router.post("/transcribe", response_model=VoiceTranscribeResponse)
async def transcribe_elf_voice_api(
    file: UploadFile = File(...),
    language: str | None = Query(default=None),
) -> VoiceTranscribeResponse:
    return await transcribe_audio_file(file, language=language)


@router.post("/speak")
def speak_elf_voice_api(
    payload: VoiceSpeakRequest,
    session: Session = Depends(get_session),
) -> Response:
    audio = synthesize_bubble_voice(
        session,
        text=payload.text,
        emoji=payload.emoji,
        profile_id=payload.profile_id,
    )
    return Response(
        content=audio.content,
        media_type=audio.media_type,
        headers={"Cache-Control": "no-store"},
    )
