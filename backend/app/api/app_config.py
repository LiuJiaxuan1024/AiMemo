from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.services.elf_voice_mode_service import get_elf_voice_mode_enabled


router = APIRouter(prefix="/config", tags=["config"])


@router.get("/runtime")
def get_runtime_config_api(session: Session = Depends(get_session)) -> dict:
    return {
        "elf": {
            "voice_mode_enabled": get_elf_voice_mode_enabled(session),
        },
    }
