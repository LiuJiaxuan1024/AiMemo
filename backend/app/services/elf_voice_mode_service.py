from __future__ import annotations

from sqlmodel import Session

from app.services.runtime_config_service import (
    get_effective_runtime_config,
    set_persistent_runtime_config,
    set_runtime_config,
)


_elf_voice_mode_enabled = False
ELF_VOICE_MODE_CONFIG_PATH = "elf.voice.mode"


def get_elf_voice_mode_enabled(session: Session | None = None) -> bool:
    if session is not None:
        return bool(get_effective_runtime_config(session, ELF_VOICE_MODE_CONFIG_PATH, False))
    return _elf_voice_mode_enabled


def set_elf_voice_mode_enabled(enabled: bool, session: Session | None = None) -> bool:
    global _elf_voice_mode_enabled
    _elf_voice_mode_enabled = bool(enabled)
    if session is not None:
        set_runtime_config(session, ELF_VOICE_MODE_CONFIG_PATH, _elf_voice_mode_enabled)
    return _elf_voice_mode_enabled


def set_elf_voice_mode_enabled_persistent(enabled: bool, session: Session) -> bool:
    global _elf_voice_mode_enabled
    _elf_voice_mode_enabled = bool(enabled)
    set_persistent_runtime_config(session, ELF_VOICE_MODE_CONFIG_PATH, _elf_voice_mode_enabled)
    return _elf_voice_mode_enabled
