from __future__ import annotations

import json

from app.agent.commands.schemas import CommandResult


COMMAND_RESULT_BLOCK = "aimemo-command-result"


def serialize_command_result(result: CommandResult) -> str:
    payload = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return f"{result.message}\n\n```{COMMAND_RESULT_BLOCK}\n{payload}\n```"
