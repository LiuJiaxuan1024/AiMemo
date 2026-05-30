from sqlmodel import Session, SQLModel, create_engine

from app.models.voice_profile import VoiceProfile
from app.schemas.voice import VoiceProfileCreate
from app.services.voice_profile_service import (
    activate_voice_profile,
    create_voice_profile,
    ensure_default_voice_profile,
    list_voice_profiles,
    save_designed_profile,
)


def _session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_default_voice_profile_is_created_once() -> None:
    with _session() as session:
        first = ensure_default_voice_profile(session)
        second = ensure_default_voice_profile(session)

        assert first.id == second.id
        assert first.is_active is True
        assert first.status == "ready"
        assert first.remote_voice_id == "Cherry"


def test_activate_voice_profile_keeps_single_active_profile() -> None:
    with _session() as session:
        ensure_default_voice_profile(session)
        custom = create_voice_profile(
            session,
            VoiceProfileCreate(
                name="暖糖",
                source_type="designed",
                status="ready",
                remote_voice_id="voice_custom",
            ),
        )

        activated = activate_voice_profile(session, custom.id)
        profiles = list_voice_profiles(session)

        assert activated.id == custom.id
        assert [profile.is_active for profile in profiles].count(True) == 1
        assert next(profile for profile in profiles if profile.id == custom.id).is_active is True


def test_draft_profile_cannot_be_activated() -> None:
    with _session() as session:
        draft = create_voice_profile(
            session,
            VoiceProfileCreate(
                name="草稿",
                source_type="draft",
                status="draft",
            ),
        )

        try:
            activate_voice_profile(session, draft.id)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 409
        else:
            raise AssertionError("draft profile should not be activatable")


def test_designed_profile_records_design_and_target_models() -> None:
    with _session() as session:
        profile = save_designed_profile(
            session,
            name="暖糖",
            description="温暖清澈",
            voice_prompt="温暖清澈的桌面精灵声线",
            style_prompt="自然",
            preview_text="今天也一起把事情做好吧。",
            remote_voice_id="qwen-tts-vd-memo_elf-voice",
            status_value="ready",
        )

        assert profile.remote_model == "qwen-voice-design"
        assert profile.remote_target_model
        assert profile.remote_voice_id == "qwen-tts-vd-memo_elf-voice"
