from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.note import utc_now


class VoiceProfile(SQLModel, table=True):
    """A reusable remote voice profile for Memo Elf speech playback."""

    __tablename__ = "voice_profiles"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(default="", index=True, max_length=120)
    description: str = ""
    voice_prompt: str = ""
    style_prompt: str = ""
    preview_text: str = ""
    language: str = Field(default="auto", index=True, max_length=24)
    speed: float = Field(default=1.0)
    energy: float = Field(default=1.0)
    emotion_bias: str = Field(default="{}")
    remote_provider: str = Field(default="aliyun_dashscope", index=True, max_length=80)
    remote_model: str = Field(default="", index=True, max_length=120)
    remote_target_model: str = Field(default="", max_length=120)
    remote_voice_id: str = Field(default="", index=True, max_length=200)
    source_type: str = Field(default="builtin", index=True, max_length=24)
    status: str = Field(default="ready", index=True, max_length=24)
    last_error: str = ""
    is_active: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
