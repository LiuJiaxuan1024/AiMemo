import json
from pathlib import Path
import re
from typing import Any

import pytest
from sqlmodel import Session

from app.agent.graphs.memory_chat.graph import run_memory_chat_graph
from app.models.chat_message import ChatMessage
from app.models.knowledge import ConversationKnowledgeMount, KnowledgeChunk, KnowledgeDocument, KnowledgeSpace
from app.models.long_term_memory import LongTermMemory
from app.models.note import Note
from app.models.note_chunk import NoteChunk
from app.rag.hashing import content_hash
from app.rag.search import search_notes_keyword
from app.schemas.conversation import ConversationCreate
from app.services.conversation_service import create_conversation
from app.services.knowledge_search_service import search_mounted_knowledge as real_search_mounted_knowledge


EVAL_CASES_PATH = Path(__file__).parent / "evals" / "memory_retrieval_cases.jsonl"


def _load_eval_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(EVAL_CASES_PATH.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        payload = json.loads(line)
        payload["_line_number"] = line_number
        cases.append(payload)
    return cases


@pytest.mark.parametrize("case", _load_eval_cases(), ids=lambda case: str(case["id"]))
def test_memory_retrieval_eval_case(
    case: dict[str, Any],
    session: Session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
):
    """固定 eval：验证记忆/检索证据是否进入本轮 Memory Chat 上下文。"""

    conversation = create_conversation(
        session,
        ConversationCreate(title=f"eval:{case['id']}"),
    )
    seed = dict(case.get("seed") or {})
    _seed_notes(session, seed.get("notes") or [])
    _seed_long_term_memories(session, seed.get("memories") or [])
    _seed_recent_messages(session, conversation_id=conversation.id or 0, messages=seed.get("recent_messages") or [])
    _seed_knowledge_spaces(
        session,
        conversation_id=conversation.id or 0,
        spaces=seed.get("knowledge_spaces") or [],
        case_id=str(case["id"]),
    )
    session.commit()

    def keyword_retriever(current_session: Session, *, query: str, limit: int):
        return search_notes_keyword(current_session, query=query, limit=limit)

    def mounted_keyword_search(
        current_session: Session,
        *,
        conversation_id: int,
        query: str,
        top_k: int = 5,
        mode: str = "hybrid",  # noqa: ARG001
        per_document_limit: int = 3,
        embedding_generator=None,  # noqa: ARG001
    ):
        return real_search_mounted_knowledge(
            current_session,
            conversation_id=conversation_id,
            query=query,
            top_k=top_k,
            mode="keyword",
            per_document_limit=per_document_limit,
        )

    monkeypatch.setattr(
        "app.agent.graphs.memory_chat.nodes.search_mounted_knowledge",
        mounted_keyword_search,
    )

    result = run_memory_chat_graph(
        conversation_id=conversation.id or 0,
        user_message=str(case["question"]),
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / f"{case['id']}.db"),
        retriever=keyword_retriever,
        answer_generator=lambda *_args: f"[eval answer:{case['id']}]",
    )

    _assert_eval_expectations(case, result)


def _seed_notes(session: Session, notes: list[dict[str, Any]]) -> None:
    for note_index, payload in enumerate(notes):
        content = str(payload.get("content") or "\n".join(payload.get("chunks") or [])).strip()
        note = Note(
            title=str(payload.get("title") or f"Eval Note {note_index + 1}"),
            content=content,
            summary=str(payload.get("summary") or ""),
            tags=str(payload.get("tags") or ""),
            content_hash=content_hash(content or f"eval-note-{note_index}"),
            status=str(payload.get("status") or "active"),
            processing_status="completed",
        )
        session.add(note)
        session.flush()
        chunks = payload.get("chunks") or [content]
        for chunk_index, chunk_text in enumerate(chunks):
            text = str(chunk_text).strip()
            session.add(
                NoteChunk(
                    note_id=note.id or 0,
                    chunk_index=chunk_index,
                    content=text,
                    content_hash=content_hash(f"{note.id}:{chunk_index}:{text}"),
                    token_count=max(1, len(text) // 2),
                    embedding_status="pending",
                )
            )


def _seed_long_term_memories(session: Session, memories: list[dict[str, Any]]) -> None:
    for index, payload in enumerate(memories):
        content = str(payload["content"])
        session.add(
            LongTermMemory(
                level=int(payload.get("level") or 4),
                category=str(payload.get("category") or "fact"),
                memory_key=str(payload.get("memory_key") or f"eval.memory.{index}"),
                content=content,
                summary=str(payload.get("summary") or ""),
                importance=float(payload.get("importance", 0.8)),
                confidence=float(payload.get("confidence", 0.8)),
                reinforcement_count=int(payload.get("reinforcement_count") or 1),
                evidence_count=int(payload.get("evidence_count") or 1),
                evidence_source_ids=json.dumps(payload.get("evidence_source_ids") or [], ensure_ascii=False),
                metadata_json=json.dumps(payload.get("metadata") or {}, ensure_ascii=False),
                source_type=str(payload.get("source_type") or "eval"),
                source_id=payload.get("source_id"),
                status=str(payload.get("status") or "active"),
                content_hash=content_hash(f"eval-memory:{content}"),
            )
        )


def _seed_recent_messages(
    session: Session,
    *,
    conversation_id: int,
    messages: list[dict[str, Any]],
) -> None:
    parent_id: int | None = None
    for payload in messages:
        message = ChatMessage(
            conversation_id=conversation_id,
            role=str(payload.get("role") or "user"),
            content=str(payload.get("content") or ""),
            parent_id=parent_id,
            status=str(payload.get("status") or "completed"),
        )
        session.add(message)
        session.flush()
        parent_id = message.id


def _seed_knowledge_spaces(
    session: Session,
    *,
    conversation_id: int,
    spaces: list[dict[str, Any]],
    case_id: str,
) -> None:
    for space_index, payload in enumerate(spaces):
        space = KnowledgeSpace(
            name=str(payload.get("name") or f"Eval Space {space_index + 1}"),
            description=str(payload.get("description") or ""),
            status=str(payload.get("status") or "active"),
        )
        session.add(space)
        session.flush()
        if bool(payload.get("mounted")):
            session.add(
                ConversationKnowledgeMount(
                    conversation_id=conversation_id,
                    space_id=space.id or 0,
                    scope_note=f"eval:{case_id}",
                )
            )
        for doc_index, document_payload in enumerate(payload.get("documents") or []):
            document = KnowledgeDocument(
                space_id=space.id or 0,
                title=str(document_payload.get("title") or f"Eval Document {doc_index + 1}"),
                source_type=str(document_payload.get("source_type") or "eval"),
                original_filename=str(document_payload.get("original_filename") or f"{case_id}-{space_index}-{doc_index}.md"),
                content_hash=content_hash(f"{case_id}:{space_index}:{doc_index}"),
                status=str(document_payload.get("status") or "ready"),
            )
            session.add(document)
            session.flush()
            for chunk_index, chunk_text in enumerate(document_payload.get("chunks") or []):
                text = str(chunk_text).strip()
                session.add(
                    KnowledgeChunk(
                        space_id=space.id or 0,
                        document_id=document.id or 0,
                        chunk_index=chunk_index,
                        text=text,
                        heading_path=json.dumps(document_payload.get("heading_path") or [], ensure_ascii=False),
                        content_hash=content_hash(f"{document.id}:{chunk_index}:{text}"),
                        token_count=max(1, len(text) // 2),
                        embedding_status="completed",
                    )
                )


def _assert_eval_expectations(case: dict[str, Any], result: dict[str, Any]) -> None:
    expected = dict(case.get("expected") or {})
    case_id = str(case["id"])
    prompt_context = str(result.get("prompt_context") or "")
    actual_sources = _collect_actual_sources(result)

    if "needs_retrieval" in expected:
        assert bool(result.get("needs_retrieval")) is bool(expected["needs_retrieval"]), case_id
    if "retrieval_grade" in expected:
        assert result.get("retrieval_grade") == expected["retrieval_grade"], case_id
    if "min_retrieved_chunks" in expected:
        assert len(result.get("retrieved_chunks") or []) >= int(expected["min_retrieved_chunks"]), case_id
    if "min_knowledge_chunks" in expected:
        assert len(result.get("knowledge_retrieved_chunks") or []) >= int(expected["min_knowledge_chunks"]), case_id

    for text in expected.get("must_include_text") or []:
        assert str(text) in prompt_context, f"{case_id}: expected prompt_context to include {text!r}"
    for text in expected.get("must_not_include_text") or []:
        assert str(text) not in prompt_context, f"{case_id}: expected prompt_context to exclude {text!r}"

    for source in expected.get("must_hit_sources") or []:
        assert _source_matches_any(source, actual_sources), _format_missing_source_message(case, source, actual_sources)
    for source in expected.get("must_not_hit_sources") or []:
        assert not _source_matches_any(source, actual_sources), _format_forbidden_source_message(case, source, actual_sources)


def _collect_actual_sources(result: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for chunk in result.get("retrieved_chunks") or []:
        sources.append(
            {
                "type": "note",
                "note_id": chunk.get("note_id"),
                "title": chunk.get("note_title"),
                "chunk_id": chunk.get("chunk_id"),
                "chunk_text": chunk.get("content"),
                "content_hash": chunk.get("content_hash"),
                "score": chunk.get("score"),
            }
        )
    for chunk in result.get("knowledge_retrieved_chunks") or []:
        sources.append(
            {
                "type": "knowledge",
                "space_id": chunk.get("space_id"),
                "space": chunk.get("space_name"),
                "document_id": chunk.get("document_id"),
                "document": chunk.get("document_title"),
                "chunk_id": chunk.get("chunk_id"),
                "chunk_text": chunk.get("text"),
                "score": chunk.get("score"),
                "score_source": chunk.get("score_source"),
                "retrieval_phase": chunk.get("retrieval_phase"),
            }
        )

    l4_layer = result.get("context_l4_layer") if isinstance(result.get("context_l4_layer"), dict) else {}
    l4_content = str(l4_layer.get("content") or "")
    for line in l4_content.splitlines():
        normalized = line.strip()
        if not normalized or "暂无已整理的核心长期记忆" in normalized:
            continue
        memory_key_match = re.search(r"key=([^,\]]+)", normalized)
        category_match = re.search(r"\[([^,\]]+)", normalized)
        sources.append(
            {
                "type": "memory",
                "memory_key": memory_key_match.group(1).strip() if memory_key_match else "",
                "category": category_match.group(1).strip(" -") if category_match else "",
                "content": normalized,
            }
        )

    for layer_key in ["context_l1_layer", "context_l0_adjacent_layer"]:
        layer = result.get(layer_key) if isinstance(result.get(layer_key), dict) else {}
        content = str(layer.get("content") or "")
        if content:
            sources.append(
                {
                    "type": "recent_message",
                    "layer": layer_key,
                    "content": content,
                }
            )
    return sources


def _source_matches_any(expected: dict[str, Any], actual_sources: list[dict[str, Any]]) -> bool:
    return any(_source_matches(expected, actual) for actual in actual_sources)


def _source_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    source_type = str(expected.get("type") or "")
    if source_type != str(actual.get("type") or ""):
        return False
    if source_type == "note":
        return _matches_common_source_fields(
            expected,
            actual,
            exact_fields=[("title", "title"), ("note_id", "note_id"), ("chunk_id", "chunk_id")],
            text_field="chunk_text",
        )
    if source_type == "knowledge":
        return _matches_common_source_fields(
            expected,
            actual,
            exact_fields=[
                ("space", "space"),
                ("space_id", "space_id"),
                ("document", "document"),
                ("document_id", "document_id"),
                ("chunk_id", "chunk_id"),
            ],
            text_field="chunk_text",
        )
    if source_type == "memory":
        return _matches_common_source_fields(
            expected,
            actual,
            exact_fields=[("memory_key", "memory_key"), ("category", "category")],
            text_field="content",
        )
    if source_type == "recent_message":
        return _matches_common_source_fields(
            expected,
            actual,
            exact_fields=[("layer", "layer")],
            text_field="content",
        )
    return False


def _matches_common_source_fields(
    expected: dict[str, Any],
    actual: dict[str, Any],
    *,
    exact_fields: list[tuple[str, str]],
    text_field: str,
) -> bool:
    for expected_key, actual_key in exact_fields:
        if expected_key in expected and str(actual.get(actual_key) or "") != str(expected[expected_key]):
            return False
    if "chunk_text" in expected and str(expected["chunk_text"]) not in str(actual.get(text_field) or ""):
        return False
    if "content" in expected and str(expected["content"]) not in str(actual.get(text_field) or ""):
        return False
    if "min_score" in expected:
        try:
            if float(actual.get("score") or 0) < float(expected["min_score"]):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _format_missing_source_message(
    case: dict[str, Any],
    source: dict[str, Any],
    actual_sources: list[dict[str, Any]],
) -> str:
    return (
        f"{case['id']}: expected source was not hit\n"
        f"question: {case.get('question')}\n"
        f"missing source: {json.dumps(source, ensure_ascii=False, sort_keys=True)}\n"
        f"actual sources:\n{_format_actual_sources(actual_sources)}"
    )


def _format_forbidden_source_message(
    case: dict[str, Any],
    source: dict[str, Any],
    actual_sources: list[dict[str, Any]],
) -> str:
    return (
        f"{case['id']}: forbidden source was hit\n"
        f"question: {case.get('question')}\n"
        f"forbidden source: {json.dumps(source, ensure_ascii=False, sort_keys=True)}\n"
        f"actual sources:\n{_format_actual_sources(actual_sources)}"
    )


def _format_actual_sources(actual_sources: list[dict[str, Any]]) -> str:
    if not actual_sources:
        return "  <none>"
    lines: list[str] = []
    for source in actual_sources:
        compact = dict(source)
        for key in ["chunk_text", "content"]:
            if key in compact and isinstance(compact[key], str) and len(compact[key]) > 90:
                compact[key] = f"{compact[key][:87]}..."
        lines.append(f"  - {json.dumps(compact, ensure_ascii=False, sort_keys=True)}")
    return "\n".join(lines)
