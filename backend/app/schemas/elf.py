from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


ElfMood = Literal["idle", "thinking", "working", "success", "warning", "error", "talking"]
ElfMotion = Literal[
    "breathe",
    "blink",
    "nod",
    "look",
    "thinking",
    "working",
    "success",
    "error",
    "dragging",
]
ElfEventSource = Literal["jobs", "chat", "memory", "graph", "workshop", "system"]


class ElfEventCreate(BaseModel):
    """后端发布给精灵的轻量事件。

    参数：
      source: 事件来源，用于桌面精灵决定图标、语气或调试分组。
      mood: 精灵主情绪，第一版直接映射到角色表情。
      motion: 可选动作，第一版用于 CSS 动效，后续可映射到 Live2D motion。
      message: 展示给用户的气泡文本；为空时只更新状态不说话。
      priority: 优先级，数值越大越重要，消费端可用它决定是否打断当前气泡。
      ttl_ms: 气泡建议显示时长；消费端可以根据自己的交互策略裁剪。
      dedupe_key: 去重键，避免同类事件在短时间内刷屏。
      metadata: 调试和行为扩展字段，不参与第一版展示逻辑。
    """

    source: ElfEventSource
    mood: ElfMood
    motion: ElfMotion | None = None
    message: str | None = Field(default=None, max_length=240)
    priority: int = Field(default=0, ge=0, le=100)
    ttl_ms: int | None = Field(default=None, ge=500, le=30000)
    dedupe_key: str | None = Field(default=None, max_length=160)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ElfEventRead(ElfEventCreate):
    id: int
    created_at: datetime


class ElfEventListRead(BaseModel):
    events: list[ElfEventRead]
    latest_id: int
