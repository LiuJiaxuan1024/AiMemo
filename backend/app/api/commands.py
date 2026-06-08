from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.agent.commands.registry import list_command_schemas
from app.agent.commands.router import execute_slash_command
from app.agent.commands.schemas import CommandExecuteRequest, CommandExecuteResponse, CommandListResponse
from app.core.database import get_session
from app.services.conversation_service import get_conversation


router = APIRouter(prefix="/conversations", tags=["commands"])


@router.get("/{conversation_id}/commands", response_model=CommandListResponse)
def list_conversation_commands_api(
    conversation_id: int,
    session: Session = Depends(get_session),
) -> CommandListResponse:
    # conversation_id is part of the endpoint because visibility can depend on conversation state.
    get_conversation(session, conversation_id)
    return CommandListResponse(items=list_command_schemas(session))


@router.post("/{conversation_id}/commands", response_model=CommandExecuteResponse)
def execute_conversation_command_api(
    conversation_id: int,
    payload: CommandExecuteRequest,
    session: Session = Depends(get_session),
) -> CommandExecuteResponse:
    return execute_slash_command(
        session,
        conversation_id=conversation_id,
        raw_command=payload.command,
        parent_message_id=payload.parent_message_id,
    )
