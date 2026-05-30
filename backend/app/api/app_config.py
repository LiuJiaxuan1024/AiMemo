from fastapi import APIRouter

from app.core.config import get_project_config_value


router = APIRouter(prefix="/config", tags=["config"])


@router.get("/runtime")
def get_runtime_config_api() -> dict:
    return {
        "elf": {
            "enabled": bool(get_project_config_value("elf.enabled", True, reload=True)),
        },
    }
