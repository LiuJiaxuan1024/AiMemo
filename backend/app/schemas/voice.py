from datetime import datetime

from pydantic import BaseModel, Field


class VoiceProfileRead(BaseModel):
    id: int
    name: str
    description: str
    voice_prompt: str
    style_prompt: str
    preview_text: str
    language: str
    speed: float
    energy: float
    emotion_bias: dict
    remote_provider: str
    remote_model: str
    remote_target_model: str
    remote_voice_id: str
    source_type: str
    status: str
    last_error: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class VoiceProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    voice_prompt: str = ""
    style_prompt: str = ""
    preview_text: str = ""
    language: str = Field(default="auto", max_length=24)
    speed: float = 1.0
    energy: float = 1.0
    emotion_bias: dict = Field(default_factory=dict)
    remote_provider: str = Field(default="aliyun_dashscope", max_length=80)
    remote_model: str = Field(default="", max_length=120)
    remote_target_model: str = Field(default="", max_length=120)
    remote_voice_id: str = Field(default="", max_length=200)
    source_type: str = Field(default="draft", max_length=24)
    status: str = Field(default="draft", max_length=24)


class VoiceProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    voice_prompt: str | None = None
    style_prompt: str | None = None
    preview_text: str | None = None
    language: str | None = Field(default=None, max_length=24)
    speed: float | None = None
    energy: float | None = None
    emotion_bias: dict | None = None
    remote_voice_id: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, max_length=24)


class VoicePreviewRequest(BaseModel):
    text: str | None = None
    emoji: str | None = None


class VoiceDesignRequest(BaseModel):
    description: str = Field(min_length=1, max_length=2048)
    name_hint: str | None = Field(default=None, max_length=120)
    preview_text: str | None = Field(default=None, max_length=500)
    language: str = Field(default="zh", max_length=24)


class VoiceDesignResponse(BaseModel):
    profile: VoiceProfileRead
    voice_prompt: str
    warnings: list[str] = Field(default_factory=list)


class VoiceTranscribeResponse(BaseModel):
    text: str
    language: str
    duration_ms: int | None = None
    provider: str
    model: str


class VoiceSpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    emoji: str | None = None
    profile_id: int | None = None


class ElfVoiceModeRead(BaseModel):
    enabled: bool


class ElfVoiceModeUpdate(BaseModel):
    enabled: bool
