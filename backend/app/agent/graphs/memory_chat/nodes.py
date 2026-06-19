from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlmodel import Session

from app.agent.graphs.memory_chat.answer_generation import (
    _build_model_messages,
    _drop_trailing_elf_listening_fillers,
    _normalize_elf_emoji,
    _parse_elf_bubble_parts,
    build_elf_bubble_answer_system_prompt,
    build_memory_chat_answer_system_prompt,
    generate_memory_chat_answer,
    generate_memory_chat_elf_bubble_answer,
)
from app.agent.graphs.memory_chat.attachment_context import (
    _inspect_image_attachment_payload,
    build_lx_attachment_context_node,
)
from app.agent.graphs.memory_chat.context_workers import (
    build_current_conversation_window_node,
    build_l0_adjacent_turn_node,
    build_l0_current_input_node,
    build_l1_recent_messages_node,
    build_l2_summary_node,
    build_l4_core_memory_node,
    dispatch_context_workers,
)
from app.agent.graphs.memory_chat.knowledge_context import (
    KNOWLEDGE_RETRIEVAL_PROFILES,
    KNOWLEDGE_RETRIEVAL_TRIGGERS,
    _build_knowledge_context_layer,
    _can_use_knowledge_recall_cache,
    _filter_ready_cached_knowledge_payloads,
    _format_knowledge_chunk_for_prompt,
    _format_mounted_knowledge_spaces,
    _knowledge_item_to_tool_data,
    _normalize_knowledge_retrieval_profile,
    _select_knowledge_payloads_from_cache,
    _should_retrieve_mounted_knowledge,
    _to_knowledge_chunk_payload,
    build_l3_knowledge_context_node,
)
from app.agent.graphs.memory_chat.load_turn import build_load_turn_state_node
from app.agent.graphs.memory_chat.merge_context import build_merge_prompt_context_node
from app.agent.graphs.memory_chat.persistence import _to_message_payload, build_persist_messages_node
from app.agent.graphs.memory_chat.react_agent import (
    AnswerGenerator,
    ElfBubbleAnswerGenerator,
    _ai_message_to_turn_message,
    _build_react_agent_messages,
    _build_react_agent_system_prompt,
    _coerce_elf_choice_final_answer_to_tool_call,
    _configured_local_operator_workspace_roots,
    _default_local_operator_workspace_roots,
    _extract_ai_message_content,
    _extract_ai_tool_calls,
    build_agent_node,
    build_generate_elf_bubble_answer_node,
    route_after_agent,
    route_answer_mode,
)
from app.agent.graphs.memory_chat.retrieval_context import (
    NoteRetriever,
    RetrievalPlan,
    RetrievalPlanner,
    build_l3_retrieved_memory_node,
    default_retrieval_planner,
)
from app.agent.graphs.memory_chat.runtime_helpers import (
    _complete_running_thoughts,
    _resolve_conversation_id,
    _resolve_user_message,
    _summarize_tool_observation,
    _thought,
    _tool_observation_message,
    _turn_message,
    json_dumps_compact,
)
from app.agent.graphs.memory_chat.state import (
    AgentTaskPayload,
    AgentThoughtPayload,
    AgentToolActionPayload,
    AgentToolObservationPayload,
    AgentWorldStatePayload,
    ChatMessagePayload,
    ContextLayerPayload,
    ElfBubblePayload,
    KnowledgeRetrievedChunkPayload,
    MemoryChatGraphState,
    MountedKnowledgeSpacePayload,
    RemoteTaskSessionPayload,
    RetrievedChunkPayload,
    TurnMessagePayload,
)
from app.agent.graphs.memory_chat.task_world import (
    _empty_world_state,
    _infer_acceptance_criteria,
    build_observe_tool_result_node,
    build_plan_task_node,
    build_verify_goal_node,
)
from app.agent.graphs.memory_chat.tools_runtime import (
    InspectImageAttachmentToolInput,
    KnowledgeSearchToolInput,
    WebFetchToolInput,
    WebSearchToolInput,
    _append_tool_context,
    _clean_tool_path_arguments,
    _create_knowledge_search_tool,
    _create_react_tools,
    _normalize_knowledge_search_arguments,
    _normalize_web_fetch_arguments,
    _normalize_web_search_arguments,
    _run_agent_tool_action,
    _tool_observations_to_context,
    build_tools_node,
)
from app.agent.graphs.memory_chat.user_input_interrupts import (
    RequestUserInputToolInput,
    UserInputOption,
    _create_request_user_input_tool,
    _normalize_request_user_input_arguments,
    _normalize_user_input_resume,
    _run_request_user_input_action,
)
from app.agent.graphs.memory_chat.web_context import WebSearchPlan, build_lx_web_context_node, plan_lx_web_context
from app.agent.model import get_agent_chat_model_with_tools, get_vision_chat_model
from app.services.knowledge_search_service import search_mounted_knowledge


SessionFactory = Callable[[], AbstractContextManager[Session]]

MAX_CONSECUTIVE_FAILED_TOOL_BATCHES = 3
REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"
USER_INTERRUPT_TOOL_NAMES = {REQUEST_USER_INPUT_TOOL_NAME}
INSPECT_IMAGE_ATTACHMENT_TOOL_NAME = "inspect_image_attachment"
WEB_SEARCH_TOOL_NAME = "web_search"
WEB_FETCH_TOOL_NAME = "web_fetch"
