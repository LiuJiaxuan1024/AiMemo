from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


CommandScope = Literal["turn", "conversation", "user", "system"]
CommandRisk = Literal["low", "medium", "high"]
CommandStatus = Literal["success", "failed", "noop", "pending_confirmation", "needs_input"]


class CommandOption(BaseModel):
    id: str
    label: str
    value: Any
    description: str = ""


class CommandArg(BaseModel):
    name: str
    type: str
    required: bool = True
    placeholder: str = ""
    options: list[CommandOption] = Field(default_factory=list)


class CommandVisibility(BaseModel):
    state: Literal["enabled", "disabled", "hidden"] = "enabled"
    reason: str = ""
    requires_feature: str | None = None
    developer_only: bool = False


class CommandSchema(BaseModel):
    id: str
    command: str
    title: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    category: str
    args: list[CommandArg] = Field(default_factory=list)
    scope: CommandScope
    risk: CommandRisk
    visibility: CommandVisibility = Field(default_factory=CommandVisibility)
    executor: str
    reload: list[str] = Field(default_factory=list)
    result_view: str = "status"


class CommandListResponse(BaseModel):
    items: list[CommandSchema]


class CommandExecuteRequest(BaseModel):
    command: str = Field(min_length=1, max_length=1000)
    parent_message_id: int | None = None


class CommandResult(BaseModel):
    source: Literal["command_router"] = "command_router"
    type: Literal["command_result"] = "command_result"
    command: str
    command_id: str = ""
    status: CommandStatus
    scope: CommandScope = "turn"
    changed: bool = False
    target: str = ""
    old_value: Any | None = None
    new_value: Any | None = None
    message: str
    details: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    audit_id: str | None = None
    rollback_command: str | None = None


class CommandExecuteResponse(BaseModel):
    result: CommandResult
    user_message: dict
    assistant_message: dict
