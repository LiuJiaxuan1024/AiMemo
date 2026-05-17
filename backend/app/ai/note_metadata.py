from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.model import get_agent_chat_model
from app.ai.json_utils import parse_json_object
from app.ai.prompts import NOTE_METADATA_SYSTEM_PROMPT, build_note_metadata_user_prompt


class NoteMetadata(BaseModel):
    title: str = Field(default="", max_length=80)
    summary: str = Field(default="", max_length=240)
    tags: list[str] = Field(default_factory=list)


def _normalize_metadata(metadata: NoteMetadata) -> NoteMetadata:
    return NoteMetadata(
        title=metadata.title.strip()[:80],
        summary=metadata.summary.strip()[:240],
        tags=[tag.strip()[:24] for tag in metadata.tags if tag.strip()][:6],
    )


def generate_note_metadata(content: str) -> NoteMetadata:
    llm = get_agent_chat_model()
    response = llm.invoke(
        [
            SystemMessage(content=NOTE_METADATA_SYSTEM_PROMPT),
            HumanMessage(content=build_note_metadata_user_prompt(content)),
        ]
    )
    payload = parse_json_object(str(response.content))
    return _normalize_metadata(NoteMetadata.model_validate(payload))
