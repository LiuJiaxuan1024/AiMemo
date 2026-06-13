from __future__ import annotations

import base64
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from html import escape
import json
from pathlib import Path
import re

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from app.models.chat_attachment import ChatAttachment
from app.models.chat_message import ChatMessage
from app.models.chat_turn import ChatTurn
from app.models.conversation import Conversation
from app.schemas.conversation import ConversationExportRequest


MAX_EMBEDDED_IMAGE_BYTES = 3 * 1024 * 1024


@dataclass(frozen=True)
class FollowupTurn:
    question: str
    assistant: ChatMessage | None
    timestamp: datetime
    status: str


@dataclass
class FollowupThread:
    segment_id: str
    original_text: str
    turns: list[FollowupTurn]


def export_conversation_html(
    session: Session,
    conversation_id: int,
    payload: ConversationExportRequest,
) -> tuple[str, str]:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
    ).all()
    if not messages:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="这个对话还没有可导出的消息。")

    by_id = {int(message.id or 0): message for message in messages if message.id is not None}
    requested_ids = [int(message_id) for message_id in payload.message_ids if int(message_id) > 0]
    requested_set = set(requested_ids)
    if not payload.include_all:
        if not requested_set:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请选择要导出的消息。")
        missing = [message_id for message_id in requested_ids if message_id not in by_id]
        if missing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"消息不属于当前对话：{missing[0]}")

    segment_followup_user_ids = {
        int(message.id or 0)
        for message in messages
        if message.role == "user" and _parse_segment_followup_payload(message.content)
    }
    segment_followup_assistant_ids = {
        int(message.id or 0)
        for message in messages
        if message.role == "assistant" and message.parent_id in segment_followup_user_ids
    }
    main_messages = [
        message
        for message in messages
        if (message.id or 0) not in segment_followup_user_ids
        and (message.id or 0) not in segment_followup_assistant_ids
    ]
    if not payload.include_all:
        main_messages = [message for message in main_messages if int(message.id or 0) in requested_set]
    if not main_messages:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="没有可见消息可导出。")

    main_ids = [int(message.id or 0) for message in main_messages if message.id is not None]
    followups_by_source = (
        _build_followup_threads(messages, source_message_ids=set(main_ids))
        if payload.include_followups
        else {}
    )
    attachment_message_ids = set(main_ids)
    assistant_ids_for_graphs = {
        int(message.id or 0)
        for message in main_messages
        if message.role == "assistant" and message.id is not None
    }
    for threads in followups_by_source.values():
        for thread in threads:
            for turn in thread.turns:
                if turn.assistant and turn.assistant.id is not None:
                    assistant_ids_for_graphs.add(int(turn.assistant.id))
                    attachment_message_ids.add(int(turn.assistant.id))

    attachments_by_message_id = _load_attachments_by_message_id(
        session,
        conversation_id=conversation_id,
        message_ids=attachment_message_ids,
    )
    graphs_by_assistant_id = (
        _load_graphs_by_assistant_message_id(
            session,
            conversation_id=conversation_id,
            assistant_message_ids=assistant_ids_for_graphs,
        )
        if payload.include_graphs
        else {}
    )

    title = conversation.title.strip() or f"对话 {conversation.id}"
    html = _render_export_html(
        conversation=conversation,
        title=title,
        messages=main_messages,
        attachments_by_message_id=attachments_by_message_id,
        followups_by_source=followups_by_source,
        graphs_by_assistant_id=graphs_by_assistant_id,
        include_graphs=payload.include_graphs,
    )
    return html, f"{_safe_filename(title)}-chat-export.html"


def _build_followup_threads(
    messages: list[ChatMessage],
    *,
    source_message_ids: set[int],
) -> dict[int, list[FollowupThread]]:
    assistant_by_parent = {
        int(message.parent_id): message
        for message in messages
        if message.role == "assistant" and message.parent_id is not None
    }
    grouped: dict[int, dict[str, FollowupThread]] = defaultdict(dict)
    for message in messages:
        if message.role != "user" or message.id is None:
            continue
        payload = _parse_segment_followup_payload(message.content)
        if payload is None:
            continue
        source_message_id = int(payload["source_message_id"])
        if source_message_id not in source_message_ids:
            continue
        segment_id = str(payload.get("segment_id") or _create_segment_id(str(payload["original_text"])))
        threads = grouped[source_message_id]
        thread = threads.get(segment_id)
        if thread is None:
            thread = FollowupThread(
                segment_id=segment_id,
                original_text=str(payload["original_text"]),
                turns=[],
            )
            threads[segment_id] = thread
        assistant = assistant_by_parent.get(int(message.id))
        thread.turns.append(
            FollowupTurn(
                question=str(payload["user_question"]),
                assistant=assistant,
                timestamp=message.created_at,
                status="failed" if assistant and assistant.status == "failed" else "answered" if assistant else "pending",
            )
        )
    return {
        source_message_id: list(threads.values())
        for source_message_id, threads in grouped.items()
    }


def _load_attachments_by_message_id(
    session: Session,
    *,
    conversation_id: int,
    message_ids: set[int],
) -> dict[int, list[ChatAttachment]]:
    if not message_ids:
        return {}
    attachments = session.exec(
        select(ChatAttachment)
        .where(ChatAttachment.conversation_id == conversation_id)
        .where(col(ChatAttachment.message_id).in_(message_ids))
        .order_by(ChatAttachment.created_at, ChatAttachment.id)
    ).all()
    grouped: dict[int, list[ChatAttachment]] = defaultdict(list)
    for attachment in attachments:
        if attachment.message_id is not None:
            grouped[int(attachment.message_id)].append(attachment)
    return dict(grouped)


def _load_graphs_by_assistant_message_id(
    session: Session,
    *,
    conversation_id: int,
    assistant_message_ids: set[int],
) -> dict[int, ChatTurn]:
    if not assistant_message_ids:
        return {}
    turns = session.exec(
        select(ChatTurn)
        .where(ChatTurn.conversation_id == conversation_id)
        .where(col(ChatTurn.assistant_message_id).in_(assistant_message_ids))
        .order_by(ChatTurn.created_at, ChatTurn.id)
    ).all()
    return {
        int(turn.assistant_message_id): turn
        for turn in turns
        if turn.assistant_message_id is not None
    }


def _render_export_html(
    *,
    conversation: Conversation,
    title: str,
    messages: list[ChatMessage],
    attachments_by_message_id: dict[int, list[ChatAttachment]],
    followups_by_source: dict[int, list[FollowupThread]],
    graphs_by_assistant_id: dict[int, ChatTurn],
    include_graphs: bool,
) -> str:
    exported_at = datetime.now().astimezone().isoformat(timespec="seconds")
    export_data = _build_export_data(
        conversation=conversation,
        title=title,
        exported_at=exported_at,
        messages=messages,
        followups_by_source=followups_by_source,
        graphs_by_assistant_id=graphs_by_assistant_id,
        include_graphs=include_graphs,
    )
    body = "\n".join(
        _render_message(
            message,
            attachments=attachments_by_message_id.get(int(message.id or 0), []),
            followup_threads=followups_by_source.get(int(message.id or 0), []),
            graphs_by_assistant_id=graphs_by_assistant_id,
            include_graphs=include_graphs,
        )
        for message in messages
    )
    script = _render_export_scripts(export_data)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)} - AiMemo 对话导出</title>
  <style>{_EXPORT_CSS}</style>
</head>
<body>
  <main class="export-shell">
    <header class="export-hero">
      <div>
        <p class="kicker">AiMemo Chat Export</p>
        <h1>{escape(title)}</h1>
        <p>{escape(conversation.summary or "导出的对话片段")}</p>
      </div>
      <dl>
        <div><dt>消息</dt><dd>{len(messages)}</dd></div>
        <div><dt>导出时间</dt><dd>{escape(exported_at)}</dd></div>
        <div><dt>会话</dt><dd>#{conversation.id or 0}</dd></div>
      </dl>
    </header>
    <section class="timeline" aria-label="对话内容">
      {body}
    </section>
  </main>
  <aside class="segment-followup-panel export-followup-panel" id="followup-panel" aria-label="片段追问侧栏" hidden></aside>
  <div id="followup-modal-root"></div>
  <div id="graph-drawer-root"></div>
  {script}
</body>
</html>"""


def _render_message(
    message: ChatMessage,
    *,
    attachments: list[ChatAttachment],
    followup_threads: list[FollowupThread],
    graphs_by_assistant_id: dict[int, ChatTurn],
    include_graphs: bool,
) -> str:
    message_id = int(message.id or 0)
    role_label = "用户" if message.role == "user" else "AiMemo" if message.role == "assistant" else message.role
    content = _render_markdown(message.content)
    attachments_html = _render_attachments(attachments)
    graph = graphs_by_assistant_id.get(message_id)
    action_html = _render_message_actions(
        message,
        followup_count=sum(len(thread.turns) for thread in followup_threads),
        graph=graph if include_graphs else None,
    )
    followup_attr = f' data-followup-message-id="{message_id}"' if followup_threads else ""
    return f"""
<article class="message message-{escape(message.role)}" id="message-{message_id}">
  <div class="message-frame">
    <div class="message-meta">
      <span>{escape(role_label)}</span>
      <time>{escape(_format_time(message.created_at))}</time>
    </div>
    <div class="bubble">
      <div class="markdown"{followup_attr}>{content}</div>
      {attachments_html}
    </div>
  </div>
  {action_html}
</article>"""


def _render_message_actions(
    message: ChatMessage,
    *,
    followup_count: int,
    graph: ChatTurn | None,
) -> str:
    if message.role != "assistant":
        return ""
    message_id = int(message.id or 0)
    graph_id = int(graph.id or 0) if graph and graph.id is not None else 0
    graph_disabled = " disabled" if graph_id <= 0 else ""
    return f"""
<div class="message-actions" aria-label="消息操作">
  <button class="message-action message-action--followups" data-open-followups data-message-id="{message_id}" type="button" title="查看片段追问">
    <span aria-hidden="true">?</span>
    <em>{followup_count}</em>
  </button>
  <button class="message-action message-action--graph" data-open-graph data-graph-id="{graph_id}" type="button" title="查看本轮 graph"{graph_disabled}>
    <span aria-hidden="true">G</span>
  </button>
</div>"""


def _build_export_data(
    *,
    conversation: Conversation,
    title: str,
    exported_at: str,
    messages: list[ChatMessage],
    followups_by_source: dict[int, list[FollowupThread]],
    graphs_by_assistant_id: dict[int, ChatTurn],
    include_graphs: bool,
) -> dict:
    graph_turns = {
        int(turn.id or 0): turn
        for turn in graphs_by_assistant_id.values()
        if include_graphs and turn.id is not None
    }
    return {
        "conversation": {
            "id": conversation.id or 0,
            "title": title,
            "summary": conversation.summary or "导出的对话片段",
            "thread_id": conversation.langgraph_thread_id,
            "exported_at": exported_at,
        },
        "messages": [
            {
                "id": int(message.id or 0),
                "role": message.role,
                "created_at": _format_time(message.created_at),
                "graph_id": int(graphs_by_assistant_id[int(message.id or 0)].id or 0)
                if int(message.id or 0) in graphs_by_assistant_id
                else None,
            }
            for message in messages
        ],
        "followups": {
            str(source_id): [_followup_thread_payload(thread, graphs_by_assistant_id) for thread in threads]
            for source_id, threads in followups_by_source.items()
        },
        "graphs": {
            str(graph_id): _graph_payload(turn)
            for graph_id, turn in graph_turns.items()
        },
    }


def _followup_thread_payload(
    thread: FollowupThread,
    graphs_by_assistant_id: dict[int, ChatTurn],
) -> dict:
    turns = []
    for turn in thread.turns:
        assistant_id = int(turn.assistant.id or 0) if turn.assistant and turn.assistant.id is not None else None
        graph = graphs_by_assistant_id.get(assistant_id or 0)
        turns.append(
            {
                "question": turn.question,
                "assistant_message_id": assistant_id,
                "answer_html": _render_markdown(turn.assistant.content)
                if turn.assistant is not None and turn.assistant.content.strip()
                else "",
                "timestamp": _format_time(turn.timestamp),
                "status": turn.status,
                "graph_id": int(graph.id or 0) if graph and graph.id is not None else None,
            }
        )
    return {
        "segment_id": thread.segment_id,
        "original_text": thread.original_text,
        "status": _followup_thread_status(thread),
        "turns": turns,
    }


def _followup_thread_status(thread: FollowupThread) -> str:
    statuses = [turn.status for turn in thread.turns]
    if "pending" in statuses:
        return "pending"
    if "failed" in statuses:
        return "failed"
    return "answered" if statuses else "pending"


def _graph_payload(turn: ChatTurn) -> dict:
    node_statuses = _decode_json_object(turn.node_statuses)
    debug_payload = _decode_json_object(turn.debug_payload)
    return {
        "turn_id": int(turn.id or 0),
        "conversation_id": turn.conversation_id,
        "user_message_id": turn.user_message_id,
        "assistant_message_id": turn.assistant_message_id,
        "thread_id": turn.thread_id,
        "checkpoint_id": turn.checkpoint_id,
        "status": turn.status,
        "node_statuses": node_statuses,
        "mermaid": _graph_mermaid(node_statuses),
        "subgraphs": {},
        "context_layers": _decode_json_list(turn.context_layers),
        "retrieved_chunks": _decode_json_list(turn.retrieved_chunks),
        "debug_payload": debug_payload,
        "state_history": _state_history_placeholder(turn),
        "error": turn.error,
    }


def _graph_mermaid(node_statuses: dict) -> str:
    try:
        from app.agent.graphs.memory_chat.graph import (
            get_elf_memory_chat_graph_mermaid,
            get_memory_chat_graph_mermaid,
        )

        mermaid_source = (
            get_elf_memory_chat_graph_mermaid()
            if "generate_elf_bubble_answer" in node_statuses
            else get_memory_chat_graph_mermaid()
        )
    except Exception:
        mermaid_source = "graph TD"
    return _highlight_memory_chat_mermaid(mermaid_source, node_statuses)


def _highlight_memory_chat_mermaid(mermaid: str, node_statuses: dict) -> str:
    lines = [
        mermaid.rstrip(),
        "classDef pendingNode fill:#f8fafc,stroke:#cbd5e1,color:#475569;",
        "classDef runningNode fill:#eff6ff,stroke:#2563eb,stroke-width:3px,color:#1d4ed8;",
        "classDef succeededNode fill:#ecfdf5,stroke:#10b981,stroke-width:2px,color:#047857;",
        "classDef failedNode fill:#fef2f2,stroke:#ef4444,stroke-width:3px,color:#b91c1c;",
        "classDef skippedNode fill:#fffbeb,stroke:#f59e0b,color:#92400e;",
    ]
    class_names = {
        "pending": "pendingNode",
        "running": "runningNode",
        "succeeded": "succeededNode",
        "completed": "succeededNode",
        "failed": "failedNode",
        "skipped": "skippedNode",
    }
    for node_name, node_status in node_statuses.items():
        class_name = class_names.get(str(node_status))
        if class_name:
            lines.append(f"class {node_name} {class_name};")
    lines.extend(
        [
            "classDef subgraphNode fill:#eef2ff,stroke:#7c3aed,stroke-width:3px,color:#4c1d95;",
            "class agent,tools subgraphNode;",
        ]
    )
    return "\n".join(lines)


def _state_history_placeholder(turn: ChatTurn) -> dict:
    return {
        "turn_id": int(turn.id or 0),
        "conversation_id": turn.conversation_id,
        "thread_id": turn.thread_id,
        "checkpoint_id": turn.checkpoint_id,
        "states": [],
        "export_note": "导出文件暂未嵌入 checkpoint history；graph、上下文和调试 payload 已随文件保存。",
    }


def _render_export_scripts(export_data: dict) -> str:
    return f"""
<script type="application/json" id="aimemo-export-data">{_json_script(export_data)}</script>
<script>{_EXPORT_JS}</script>"""


def _json_script(value: dict) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _render_attachments(attachments: list[ChatAttachment]) -> str:
    if not attachments:
        return ""
    items = []
    for attachment in attachments:
        label = escape(attachment.original_name or "attachment")
        size = escape(_format_size(attachment.size_bytes))
        if attachment.kind == "image":
            data_uri = _attachment_image_data_uri(attachment)
            if data_uri:
                items.append(
                    f"<figure class=\"attachment attachment-image\"><img alt=\"{label}\" src=\"{data_uri}\" /><figcaption>{label} · {size}</figcaption></figure>"
                )
                continue
        items.append(
            f"<div class=\"attachment\"><strong>{label}</strong><span>{escape(attachment.mime_type or attachment.kind)} · {size}</span></div>"
        )
    return f"<div class=\"attachments\">{''.join(items)}</div>"


def _attachment_image_data_uri(attachment: ChatAttachment) -> str:
    if attachment.size_bytes <= 0 or attachment.size_bytes > MAX_EMBEDDED_IMAGE_BYTES:
        return ""
    path = Path(attachment.storage_path)
    if not path.exists() or not path.is_file():
        return ""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > MAX_EMBEDDED_IMAGE_BYTES:
        return ""
    mime_type = attachment.mime_type or "application/octet-stream"
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def _render_markdown(value: str) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    html_parts: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    ordered_items: list[str] = []
    in_code = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            text = " ".join(line.strip() for line in paragraph).strip()
            if text:
                html_parts.append(f"<p>{_inline_markdown(text)}</p>")
            paragraph.clear()

    def flush_lists() -> None:
        if list_items:
            html_parts.append("<ul>" + "".join(f"<li>{_inline_markdown(item)}</li>" for item in list_items) + "</ul>")
            list_items.clear()
        if ordered_items:
            html_parts.append("<ol>" + "".join(f"<li>{_inline_markdown(item)}</li>" for item in ordered_items) + "</ol>")
            ordered_items.clear()

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                html_parts.append(f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>")
                code_lines.clear()
                in_code = False
            else:
                flush_paragraph()
                flush_lists()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not stripped:
            flush_paragraph()
            flush_lists()
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            flush_lists()
            level = min(4, len(heading.group(1)))
            html_parts.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
            continue
        unordered = re.match(r"^[-*]\s+(.+)$", stripped)
        if unordered:
            flush_paragraph()
            ordered_items.clear()
            list_items.append(unordered.group(1))
            continue
        ordered = re.match(r"^\d+\.\s+(.+)$", stripped)
        if ordered:
            flush_paragraph()
            list_items.clear()
            ordered_items.append(ordered.group(1))
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            flush_lists()
            html_parts.append(f"<blockquote>{_inline_markdown(stripped.lstrip('> ').strip())}</blockquote>")
            continue
        flush_lists()
        paragraph.append(line)

    if in_code:
        html_parts.append(f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>")
    flush_paragraph()
    flush_lists()
    return "".join(html_parts) or "<p></p>"


def _inline_markdown(value: str) -> str:
    text = escape(value)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    return text


def _parse_segment_followup_payload(content: str) -> dict | None:
    try:
        payload = json.loads(content)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if (
        payload.get("type") != "segment_followup"
        or not isinstance(payload.get("source_message_id"), int)
        or not isinstance(payload.get("original_text"), str)
        or not isinstance(payload.get("user_question"), str)
    ):
        return None
    return payload


def _decode_json_object(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _decode_json_list(value: str | None) -> list:
    try:
        parsed = json.loads(value or "[]")
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _create_segment_id(text: str) -> str:
    value = 2166136261
    for char in text:
        value ^= ord(char)
        value = (value * 16777619) & 0xFFFFFFFF
    return f"seg-{value:08x}"


def _safe_filename(value: str) -> str:
    normalized = re.sub(r"[\\/:*?\"<>|]+", "-", value).strip(" .")
    normalized = re.sub(r"\s+", "-", normalized)
    return normalized[:80] or "conversation"


def _format_time(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


_EXPORT_CSS = """
:root {
  color-scheme: light;
  --bg: #f8f4eb;
  --surface: #fffaf1;
  --sunken: #f1eadf;
  --ink: #24211d;
  --muted: #746d63;
  --border: #e4d8c8;
  --brand: #5f9f72;
  --brand-dark: #347455;
  --user: #609f72;
  --user-ink: #fffaf1;
  --warning: #9a6526;
  --danger: #c84d4b;
  font-family: Inter, "Segoe UI", "Microsoft YaHei", sans-serif;
}
* { box-sizing: border-box; }
body {
  background: var(--bg);
  color: var(--ink);
  margin: 0;
}
.export-shell {
  display: grid;
  gap: 22px;
  margin: 0 auto;
  max-width: 1080px;
  padding: 28px;
}
.export-hero {
  align-items: end;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: 0 18px 40px rgba(90, 67, 42, 0.12);
  display: grid;
  gap: 18px;
  grid-template-columns: minmax(0, 1fr) auto;
  padding: 24px;
}
.kicker {
  color: var(--brand-dark);
  font-size: 12px;
  font-weight: 800;
  margin: 0 0 8px;
  text-transform: uppercase;
}
h1 {
  font-family: Georgia, "Microsoft YaHei", serif;
  font-size: 30px;
  line-height: 1.15;
  margin: 0 0 8px;
}
.export-hero p {
  color: var(--muted);
  line-height: 1.6;
  margin: 0;
}
.export-hero dl {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(3, minmax(88px, auto));
  margin: 0;
}
.export-hero dl div {
  background: #fff6e6;
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 10px 12px;
}
dt {
  color: var(--muted);
  font-size: 12px;
}
dd {
  color: var(--ink);
  font-weight: 750;
  margin: 2px 0 0;
}
.timeline {
  display: grid;
  gap: 18px;
}
.message {
  display: grid;
  gap: 6px;
  max-width: min(850px, 100%);
}
.message-user {
  justify-self: end;
}
.message-assistant {
  justify-self: start;
}
.message-meta {
  align-items: center;
  color: var(--muted);
  display: flex;
  font-size: 12px;
  gap: 8px;
}
.message-user .message-meta {
  justify-content: end;
}
.message-meta span {
  color: var(--brand-dark);
  font-weight: 800;
}
.bubble {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: 0 8px 20px rgba(90, 67, 42, 0.08);
  line-height: 1.72;
  padding: 16px 18px;
}
.message-user .bubble {
  background: var(--user);
  border-color: transparent;
  color: var(--user-ink);
}
.markdown > :first-child { margin-top: 0; }
.markdown > :last-child { margin-bottom: 0; }
.markdown p { margin: 0 0 12px; }
.markdown h1, .markdown h2, .markdown h3, .markdown h4 {
  line-height: 1.35;
  margin: 14px 0 8px;
}
.markdown ul, .markdown ol {
  margin: 8px 0 12px;
  padding-left: 22px;
}
.markdown blockquote {
  background: rgba(95, 159, 114, 0.12);
  border-left: 3px solid var(--brand);
  border-radius: 8px;
  margin: 10px 0;
  padding: 8px 12px;
}
code {
  background: rgba(40, 33, 26, 0.08);
  border-radius: 5px;
  font-family: "Cascadia Code", Consolas, monospace;
  padding: 2px 5px;
}
pre {
  background: #28221c;
  border-radius: 10px;
  color: #fffaf1;
  overflow: auto;
  padding: 12px;
}
pre code {
  background: transparent;
  padding: 0;
}
.attachments {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}
.attachment {
  background: rgba(255, 255, 255, 0.52);
  border: 1px solid var(--border);
  border-radius: 10px;
  display: grid;
  gap: 3px;
  padding: 10px;
}
.message-user .attachment {
  background: rgba(255, 255, 255, 0.14);
  border-color: rgba(255, 255, 255, 0.26);
}
.attachment span {
  color: var(--muted);
  font-size: 12px;
}
.attachment-image img {
  border-radius: 8px;
  display: block;
  max-height: 360px;
  max-width: 100%;
}
.attachment-image figcaption {
  color: var(--muted);
  font-size: 12px;
  margin-top: 6px;
}
.followups {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}
.followup-thread {
  background: #f2f7ef;
  border: 1px solid rgba(95, 159, 114, 0.24);
  border-radius: 12px;
  padding: 10px 12px;
}
.followup-thread summary {
  cursor: pointer;
  display: grid;
  gap: 4px;
}
.followup-thread summary span {
  color: var(--brand-dark);
  font-size: 12px;
  font-weight: 800;
}
.followup-thread q {
  color: var(--muted);
  font-size: 13px;
}
.followup-turn {
  border-top: 1px solid rgba(95, 159, 114, 0.18);
  margin-top: 10px;
  padding-top: 10px;
}
.followup-question {
  color: var(--ink);
  font-weight: 700;
  margin: 0 0 8px;
}
.followup-question span {
  background: var(--brand);
  border-radius: 999px;
  color: #ffffff;
  display: inline-grid;
  font-size: 11px;
  height: 22px;
  margin-right: 8px;
  place-items: center;
  width: 28px;
}
.graph-card {
  background: #fff8e9;
  border: 1px solid #eadbc7;
  border-radius: 12px;
  margin-top: 14px;
  padding: 10px 12px;
}
.graph-card summary {
  align-items: center;
  cursor: pointer;
  display: flex;
  gap: 8px;
}
.graph-card summary span {
  color: var(--brand-dark);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}
.graph-card summary em {
  background: #eef7ec;
  border-radius: 999px;
  color: var(--brand-dark);
  font-size: 12px;
  font-style: normal;
  padding: 2px 8px;
}
.graph-nodes {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
}
.node {
  align-items: center;
  background: #ffffff;
  border: 1px solid var(--border);
  border-radius: 999px;
  display: inline-flex;
  gap: 6px;
  padding: 5px 9px;
}
.node em {
  color: var(--muted);
  font-size: 11px;
  font-style: normal;
}
.node-succeeded, .node-completed { border-color: rgba(95, 159, 114, 0.42); }
.node-running, .node-pending { border-color: rgba(154, 101, 38, 0.42); }
.node-failed { border-color: rgba(200, 77, 75, 0.42); }
.graph-context, .graph-evidence {
  border-top: 1px solid var(--border);
  display: grid;
  gap: 6px;
  margin-top: 12px;
  padding-top: 10px;
}
.graph-context p, .graph-evidence p {
  color: var(--muted);
  display: flex;
  gap: 10px;
  justify-content: space-between;
  margin: 0;
}
.muted {
  color: var(--muted);
}
@media (max-width: 760px) {
  .export-shell { padding: 14px; }
  .export-hero { grid-template-columns: minmax(0, 1fr); }
  .export-hero dl { grid-template-columns: minmax(0, 1fr); }
  .message, .message-user, .message-assistant { justify-self: stretch; }
}
"""

_EXPORT_CSS += """
.message {
  align-items: start;
  grid-template-columns: minmax(0, 1fr) auto;
}
.message-user {
  grid-template-columns: auto minmax(0, 1fr);
}
.message-frame {
  display: grid;
  gap: 6px;
  min-width: 0;
}
.message-user .message-frame {
  grid-column: 2;
}
.message-actions {
  align-self: end;
  display: inline-grid;
  gap: 6px;
  grid-column: 2;
  margin-bottom: 2px;
}
.message-action {
  align-items: center;
  background: #fffaf1;
  border: 1px solid #e7dac7;
  border-radius: 999px;
  box-shadow: 0 6px 14px rgba(90, 67, 42, 0.08);
  color: #5f6f60;
  cursor: pointer;
  display: inline-flex;
  font: inherit;
  font-size: 12px;
  font-weight: 800;
  gap: 4px;
  height: 34px;
  justify-content: center;
  min-width: 34px;
  padding: 0 8px;
}
.message-action:hover:not(:disabled),
.message-action.is-active {
  background: #ecf7ef;
  border-color: rgba(95, 159, 114, 0.42);
  color: var(--brand-dark);
}
.message-action:disabled {
  cursor: not-allowed;
  opacity: 0.42;
}
.message-action em {
  background: #e7f2e7;
  border-radius: 999px;
  color: var(--brand-dark);
  font-size: 10px;
  font-style: normal;
  min-width: 16px;
  padding: 1px 5px;
}
.segment-followup-mark {
  background: #fff6c7;
  border: 1px solid rgba(220, 170, 28, 0.46);
  border-radius: 5px;
  color: inherit;
  cursor: pointer;
  display: inline;
  font: inherit;
  padding: 0 3px;
}
.segment-followup-mark:hover,
.segment-followup-mark.is-active {
  background: #e9f2ff;
  border-color: #93b4f3;
  color: #347455;
}
.export-followup-panel {
  animation: segment-followup-panel-in 180ms ease-out both;
  background: #ffffff;
  border: 1px solid #dfe6f0;
  border-radius: 10px;
  bottom: 28px;
  box-shadow: 0 16px 44px -30px rgba(15, 23, 42, 0.55);
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  max-height: calc(100vh - 56px);
  overflow: hidden;
  position: fixed;
  right: 16px;
  top: 28px;
  width: min(460px, calc(100vw - 32px));
  z-index: 48;
}
.export-followup-panel[hidden] {
  display: none;
}
.segment-followup-panel__header {
  align-items: center;
  border-bottom: 1px solid #e7ecf4;
  display: flex;
  gap: 12px;
  justify-content: space-between;
  padding: 12px 12px 10px;
}
.segment-followup-panel__header h3 {
  color: #1d2433;
  font-size: 14px;
  margin: 0;
}
.segment-followup-panel__header p {
  color: #667085;
  font-size: 12px;
  margin: 3px 0 0;
}
.export-icon-button {
  align-items: center;
  background: #ffffff;
  border: 1px solid #dbe4f0;
  border-radius: 8px;
  color: #526174;
  cursor: pointer;
  display: inline-flex;
  font: inherit;
  font-size: 13px;
  height: 32px;
  justify-content: center;
  min-width: 32px;
  padding: 0 9px;
}
.export-icon-button:hover {
  background: #ecf7ef;
  border-color: rgba(95, 159, 114, 0.36);
  color: var(--brand-dark);
}
.segment-followup-panel__empty {
  align-items: center;
  color: #667085;
  display: flex;
  justify-content: center;
  min-height: 160px;
  padding: 18px;
  text-align: center;
}
.segment-followup-panel__list {
  align-content: start;
  display: grid;
  gap: 9px;
  min-height: 0;
  overflow: auto;
  padding: 10px;
  scrollbar-gutter: stable;
}
.segment-followup-panel__item {
  background: #fbfcff;
  border: 1px solid #e3eaf4;
  border-radius: 10px;
  min-width: 0;
  overflow: hidden;
}
.segment-followup-panel__item[open] {
  background: #ffffff;
  border-color: #cbdcf6;
  box-shadow: 0 8px 22px -20px rgba(37, 99, 235, 0.32);
}
.segment-followup-panel__item.is-active {
  border-color: #7c9cff;
  box-shadow: 0 0 0 2px rgba(124, 156, 255, 0.16);
}
.segment-followup-panel__summary {
  align-items: center;
  cursor: pointer;
  display: grid;
  gap: 5px 7px;
  grid-template-columns: auto minmax(0, 1fr) auto auto;
  list-style: none;
  min-width: 0;
  padding: 10px 11px;
}
.segment-followup-panel__summary::-webkit-details-marker {
  display: none;
}
.segment-followup-panel__badge {
  background: #ecf7ef;
  border-radius: 999px;
  color: var(--brand-dark);
  font-size: 12px;
  font-weight: 650;
  padding: 1px 7px;
}
.segment-followup-panel__source-text {
  color: #334155;
  font-size: 12px;
  line-height: 1.45;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.segment-followup-panel__status {
  border-radius: 999px;
  font-size: 11px;
  font-weight: 650;
  padding: 1px 7px;
}
.segment-followup-panel__status--answered { background: #ecfdf3; color: #067647; }
.segment-followup-panel__status--pending { background: #fff7ed; color: #b54708; }
.segment-followup-panel__status--failed { background: #fef3f2; color: #b42318; }
.segment-followup-panel__summary strong {
  color: #1f2937;
  font-size: 13px;
  font-weight: 700;
  grid-column: 1 / 4;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.segment-followup-panel__summary small {
  color: #94a3b8;
  font-size: 11px;
  font-weight: 700;
  grid-column: 3;
  grid-row: 2;
  justify-self: end;
}
.segment-followup-panel__expand {
  align-items: center;
  background: #ffffff;
  border: 1px solid #dbe4f0;
  border-radius: 7px;
  color: #526174;
  cursor: pointer;
  display: inline-flex;
  grid-column: 4;
  grid-row: 1;
  height: 26px;
  justify-content: center;
  width: 26px;
}
.segment-followup-panel__answer {
  border-left: 3px solid #bfdbfe;
  margin: 0 11px 11px;
  max-height: 420px;
  min-width: 0;
  overflow: auto;
  padding-left: 10px;
  padding-right: 4px;
}
.segment-followup-thread-turns,
.segment-followup-modal__turns {
  display: grid;
  gap: 10px;
}
.segment-followup-turn,
.segment-followup-modal__turn {
  display: grid;
  gap: 8px;
  min-width: 0;
  position: relative;
}
.segment-followup-turn + .segment-followup-turn,
.segment-followup-modal__turn + .segment-followup-modal__turn {
  border-top: 1px solid #edf2f7;
  padding-top: 10px;
}
.segment-followup-turn__question {
  align-items: start;
  color: #101828;
  display: grid;
  font-size: 13px;
  font-weight: 750;
  gap: 7px;
  grid-template-columns: auto minmax(0, 1fr);
  line-height: 1.45;
  margin: 0;
}
.segment-followup-turn__question span {
  background: #ecf7ef;
  border-radius: 999px;
  color: var(--brand-dark);
  font-size: 11px;
  font-weight: 800;
  padding: 1px 7px;
}
.segment-followup-turn__answer {
  color: #1d2433;
  min-width: 0;
}
.segment-followup-turn__assistant {
  align-items: start;
  display: grid;
  gap: 8px;
  grid-template-columns: minmax(0, 1fr) auto;
  min-width: 0;
}
.segment-followup-panel__pending {
  color: #64748b;
  font-size: 12.5px;
  margin: 0;
}
.segment-followup-modal-backdrop {
  align-items: center;
  background: rgba(15, 23, 42, 0.28);
  display: flex;
  inset: 0;
  justify-content: center;
  padding: 28px;
  position: fixed;
  z-index: 90;
  animation: segment-followup-modal-fade 140ms ease-out both;
}
.segment-followup-modal {
  background: #ffffff;
  border: 1px solid #d9e2ef;
  border-radius: 14px;
  box-shadow: 0 28px 90px -35px rgba(15, 23, 42, 0.55);
  display: grid;
  gap: 12px;
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  max-height: calc(100vh - 56px);
  max-width: calc(100vw - 56px);
  min-height: min(620px, calc(100vh - 56px));
  overflow: hidden;
  padding: 16px;
  width: min(1040px, calc(100vw - 56px));
  animation: segment-followup-pop 150ms ease-out both;
}
.segment-followup-modal__header {
  align-items: start;
  display: flex;
  gap: 14px;
  justify-content: space-between;
}
.segment-followup-modal__header span {
  color: var(--brand-dark);
  font-size: 12px;
  font-weight: 800;
}
.segment-followup-modal__header h3 {
  color: #101828;
  font-size: 17px;
  line-height: 1.4;
  margin: 3px 0 0;
}
.segment-followup-modal__source {
  align-items: center;
  background: #f8fbff;
  border: 1px solid #e1eaf7;
  border-radius: 10px;
  display: grid;
  gap: 10px;
  grid-template-columns: auto minmax(0, 1fr);
  min-height: 54px;
  padding: 9px 12px;
}
.segment-followup-modal__source span {
  align-items: center;
  background: #ecf7ef;
  border-radius: 999px;
  color: var(--brand-dark);
  display: inline-flex;
  font-size: 12px;
  font-weight: 700;
  height: 26px;
  padding: 0 8px;
}
.segment-followup-modal__source q {
  align-items: center;
  color: #334155;
  display: flex;
  line-height: 1.45;
  min-height: 26px;
}
.segment-followup-modal__body {
  border-left: 3px solid #bfdbfe;
  min-height: 0;
  overflow: auto;
  padding-left: 12px;
  padding-right: 4px;
}
.segment-followup-modal__readonly {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  color: #64748b;
  font-size: 12px;
  margin: 0;
  padding: 10px 12px;
}
.chat-debug-workspace {
  background: #f5f7fb;
  bottom: 0;
  border: 1px solid #dfe6f0;
  border-bottom: 0;
  border-radius: 16px 16px 0 0;
  box-shadow: 0 -22px 70px rgba(15, 23, 42, 0.26);
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  height: min(82vh, 860px);
  left: 16px;
  position: fixed;
  right: 16px;
  z-index: 120;
  animation: chat-debug-drawer-in 220ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
}
.chat-debug-workspace-header {
  align-items: center;
  background: #ffffff;
  border-bottom: 1px solid #e1e6ef;
  display: grid;
  gap: 18px;
  grid-template-columns: minmax(220px, 1fr) auto auto;
  padding: 12px 20px;
}
.chat-debug-workspace-title h2 {
  font-size: 15px;
  font-weight: 650;
  margin: 0;
}
.chat-debug-workspace-title p {
  color: #667085;
  display: flex;
  flex-wrap: wrap;
  font-size: 12px;
  gap: 10px;
  margin: 4px 0 0;
}
.chat-debug-status {
  border-radius: 999px;
  font-size: 11px;
  font-weight: 650;
  padding: 1px 8px;
}
.chat-debug-status-completed,
.chat-debug-status-succeeded,
.chat-debug-status-ok { background: #dcfce7; color: #166534; }
.chat-debug-status-running,
.chat-debug-status-pending { background: #fef3c7; color: #92400e; }
.chat-debug-status-failed,
.chat-debug-status-error { background: #fee2e2; color: #991b1b; }
.chat-debug-workspace-tabs {
  background: #f1f4fb;
  border: 1px solid #e1e6ef;
  border-radius: 10px;
  display: inline-flex;
  gap: 4px;
  padding: 4px;
}
.chat-debug-workspace-tabs button {
  align-items: center;
  background: transparent;
  border: 0;
  border-radius: 7px;
  color: #475467;
  cursor: pointer;
  display: inline-flex;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  gap: 6px;
  min-height: 30px;
  padding: 0 12px;
}
.chat-debug-workspace-tabs button.is-active {
  background: #ffffff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.12);
  color: #101828;
}
.chat-debug-workspace-body {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  padding: 16px 20px 20px;
}
.chat-debug-graph-tab {
  display: grid;
  gap: 16px;
  grid-template-columns: minmax(0, 1.5fr) minmax(280px, 0.85fr);
  min-height: 0;
  overflow: hidden;
}
.chat-debug-graph-canvas,
.chat-debug-graph-aside,
.chat-debug-panel-card {
  background: #ffffff;
  border: 1px solid #e1e6ef;
  border-radius: 12px;
  min-height: 0;
  overflow: auto;
  padding: 12px;
}
.export-graph-flow {
  display: grid;
  gap: 8px;
}
.export-graph-node {
  align-items: center;
  background: #f8fafc;
  border: 1.5px solid #e2e8f0;
  border-radius: 10px;
  color: #1e293b;
  cursor: pointer;
  display: grid;
  gap: 8px;
  grid-template-columns: minmax(0, 1fr) auto;
  min-height: 42px;
  padding: 8px 10px;
  text-align: left;
}
.export-graph-node:hover,
.export-graph-node.is-active {
  border-color: #7c9cff;
  box-shadow: 0 0 0 2px rgba(124, 156, 255, 0.16);
}
.export-graph-node em {
  border-radius: 999px;
  font-size: 11px;
  font-style: normal;
  font-weight: 700;
  padding: 2px 8px;
}
.export-graph-node.status-succeeded em,
.export-graph-node.status-completed em { background: #dcfce7; color: #166534; }
.export-graph-node.status-running em,
.export-graph-node.status-pending em { background: #fef3c7; color: #92400e; }
.export-graph-node.status-failed em { background: #fee2e2; color: #991b1b; }
.export-graph-node.status-skipped em { background: #fffbeb; color: #92400e; }
.chat-debug-aside-section {
  border: 1px solid #e1e6ef;
  border-radius: 10px;
  display: grid;
  gap: 10px;
  padding: 12px;
}
.chat-debug-aside-section header {
  align-items: center;
  display: flex;
  justify-content: space-between;
}
.chat-debug-aside-section h4 {
  font-size: 13px;
  margin: 0;
}
.export-code,
.chat-debug-aside-section pre,
.chat-debug-context-card pre {
  background: #0f172a;
  border-radius: 8px;
  color: #e2e8f0;
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: 11.5px;
  line-height: 1.55;
  margin: 0;
  max-height: 360px;
  overflow: auto;
  padding: 10px;
  white-space: pre-wrap;
  word-break: break-word;
}
.chat-debug-empty {
  align-items: center;
  color: #667085;
  display: flex;
  justify-content: center;
  min-height: 180px;
  text-align: center;
}
.chat-debug-context-tab,
.chat-debug-performance-tab,
.chat-debug-checkpoints-tab {
  display: grid;
  gap: 10px;
  min-height: 0;
  overflow: auto;
  padding-right: 4px;
}
.chat-debug-context-card,
.chat-evidence-card {
  background: #ffffff;
  border: 1px solid #e1e6ef;
  border-radius: 10px;
  padding: 10px 12px;
}
.chat-debug-context-card summary {
  align-items: center;
  cursor: pointer;
  display: flex;
  font-size: 13px;
  gap: 10px;
}
.chat-debug-context-level {
  border-radius: 6px;
  display: inline-block;
  font-size: 11px;
  font-weight: 700;
  min-width: 42px;
  padding: 2px 8px;
  text-align: center;
}
.chat-debug-context-level-0 { background: #dcfce7; color: #166534; }
.chat-debug-context-level-1 { background: #fef3c7; color: #78350f; }
.chat-debug-context-level-2 { background: #ede9fe; color: #4c1d95; }
.chat-debug-context-level-3 { background: #d1fae5; color: #065f46; }
.chat-debug-context-level-4 { background: #fce7f3; color: #831843; }
.chat-debug-performance-summary {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.chat-debug-performance-summary div {
  background: #ffffff;
  border: 1px solid #e1e6ef;
  border-radius: 10px;
  display: grid;
  gap: 4px;
  padding: 12px;
}
.chat-debug-performance-summary span {
  color: #64748b;
  font-size: 12px;
}
.chat-debug-performance-summary strong {
  color: #101828;
  font-size: 18px;
}
.chat-debug-performance-table {
  background: #ffffff;
  border-collapse: collapse;
  border-radius: 10px;
  overflow: hidden;
  width: 100%;
}
.chat-debug-performance-table th,
.chat-debug-performance-table td {
  border-bottom: 1px solid #edf2f7;
  font-size: 12px;
  padding: 8px 10px;
  text-align: left;
}
.chat-debug-json-tree {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  color: #1e293b;
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: 11.5px;
  max-height: 420px;
  overflow: auto;
  padding: 8px;
}
.chat-debug-json-tree details {
  margin: 2px 0;
}
.chat-debug-json-tree summary {
  cursor: pointer;
}
.chat-debug-json-key {
  color: #475569;
  font-weight: 700;
}
.chat-debug-json-string {
  color: #047857;
}
.chat-debug-json-scalar {
  color: #7c3aed;
}
@keyframes segment-followup-modal-fade {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes segment-followup-pop {
  from { opacity: 0; transform: translateY(4px) scale(0.98); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
@keyframes segment-followup-panel-in {
  from { opacity: 0; transform: translateX(14px); }
  to { opacity: 1; transform: translateX(0); }
}
@keyframes chat-debug-drawer-in {
  from { opacity: 0; transform: translateY(34px); }
  to { opacity: 1; transform: translateY(0); }
}
@media (max-width: 900px) {
  .chat-debug-workspace-header {
    grid-template-columns: minmax(0, 1fr);
  }
  .chat-debug-workspace-tabs {
    overflow-x: auto;
  }
  .chat-debug-graph-tab {
    grid-template-columns: minmax(0, 1fr);
  }
  .chat-debug-performance-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 760px) {
  .message,
  .message-user,
  .message-assistant {
    grid-template-columns: minmax(0, 1fr);
  }
  .message-user .message-frame {
    grid-column: 1;
  }
  .message-actions {
    grid-column: 1;
    grid-auto-flow: column;
    justify-self: end;
  }
  .segment-followup-modal {
    max-height: calc(100vh - 28px);
    max-width: calc(100vw - 28px);
    min-height: calc(100vh - 28px);
    width: calc(100vw - 28px);
  }
}
"""

_EXPORT_JS = r"""
(() => {
  const dataNode = document.getElementById("aimemo-export-data");
  const DATA = dataNode ? JSON.parse(dataNode.textContent || "{}") : {};
  const followupPanel = document.getElementById("followup-panel");
  const followupModalRoot = document.getElementById("followup-modal-root");
  const graphDrawerRoot = document.getElementById("graph-drawer-root");
  let activeFollowupMessageId = null;
  let activeFollowupSegmentId = null;
  let activeGraphId = null;
  let activeGraphTab = "graph";
  let activeGraphNode = null;

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function statusLabel(status) {
    if (status === "pending") return "生成中";
    if (status === "failed") return "失败";
    return "已回复";
  }

  function graphStatusClass(status) {
    return `chat-debug-status chat-debug-status-${String(status || "pending")}`;
  }

  function messageFollowups(messageId) {
    return DATA.followups?.[String(messageId)] || [];
  }

  function findThread(messageId, segmentId) {
    return messageFollowups(messageId).find((thread) => thread.segment_id === segmentId) || null;
  }

  function markFollowupSegments() {
    document.querySelectorAll("[data-followup-message-id]").forEach((root) => {
      const messageId = root.getAttribute("data-followup-message-id");
      const threads = messageFollowups(messageId).filter((thread) => thread.original_text);
      if (!threads.length) return;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
          const parent = node.parentElement;
          if (!parent || !node.textContent?.trim()) return NodeFilter.FILTER_REJECT;
          if (parent.closest("pre, code, button, .segment-followup-mark")) return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        },
      });
      const nodes = [];
      let node = walker.nextNode();
      while (node) {
        nodes.push(node);
        node = walker.nextNode();
      }
      for (const textNode of nodes) {
        let remaining = textNode.textContent || "";
        const pieces = [];
        while (remaining) {
          const match = earliestThreadMatch(remaining, threads);
          if (!match) {
            pieces.push(remaining);
            break;
          }
          if (match.index > 0) pieces.push(remaining.slice(0, match.index));
          pieces.push(match.thread);
          remaining = remaining.slice(match.index + match.thread.original_text.length);
        }
        if (pieces.length <= 1) continue;
        const fragment = document.createDocumentFragment();
        for (const piece of pieces) {
          if (typeof piece === "string") {
            fragment.append(document.createTextNode(piece));
            continue;
          }
          const button = document.createElement("button");
          button.className = "segment-followup-mark";
          button.dataset.messageId = String(messageId);
          button.dataset.segmentId = piece.segment_id;
          button.type = "button";
          button.textContent = piece.original_text;
          button.title = "查看这个片段的追问";
          fragment.append(button);
        }
        textNode.replaceWith(fragment);
      }
    });
  }

  function earliestThreadMatch(text, threads) {
    let best = null;
    for (const thread of threads) {
      const index = text.indexOf(thread.original_text);
      if (index < 0) continue;
      if (!best || index < best.index) best = { index, thread };
    }
    return best;
  }

  function openFollowupPanel(messageId, segmentId = null) {
    if (!followupPanel) return;
    activeFollowupMessageId = Number(messageId);
    activeFollowupSegmentId = segmentId;
    renderFollowupPanel();
    document.querySelectorAll(".message-action--followups").forEach((button) => {
      button.classList.toggle("is-active", Number(button.dataset.messageId) === activeFollowupMessageId);
    });
  }

  function closeFollowupPanel() {
    if (!followupPanel) return;
    followupPanel.hidden = true;
    activeFollowupMessageId = null;
    activeFollowupSegmentId = null;
    document.querySelectorAll(".message-action--followups").forEach((button) => button.classList.remove("is-active"));
    document.querySelectorAll(".segment-followup-mark").forEach((button) => button.classList.remove("is-active"));
  }

  function renderFollowupPanel() {
    if (!followupPanel || activeFollowupMessageId == null) return;
    const threads = messageFollowups(activeFollowupMessageId);
    followupPanel.hidden = false;
    document.querySelectorAll(".segment-followup-mark").forEach((button) => {
      button.classList.toggle(
        "is-active",
        Number(button.dataset.messageId) === activeFollowupMessageId &&
          button.dataset.segmentId === activeFollowupSegmentId,
      );
    });
    const body = threads.length
      ? `<div class="segment-followup-panel__list">${threads.map(renderFollowupPanelItem).join("")}</div>`
      : `<p class="segment-followup-panel__empty">这条回复还没有片段追问。</p>`;
    followupPanel.innerHTML = `
      <header class="segment-followup-panel__header">
        <div>
          <h3>片段追问</h3>
          <p>当前回复中的局部讨论</p>
        </div>
        <button class="export-icon-button" data-close-followups type="button" title="收起片段追问">×</button>
      </header>
      ${body}`;
  }

  function renderFollowupPanelItem(thread, index) {
    const lastTurn = thread.turns[thread.turns.length - 1] || {};
    const open = activeFollowupSegmentId === thread.segment_id || index === messageFollowups(activeFollowupMessageId).length - 1;
    return `
      <details class="segment-followup-panel__item ${activeFollowupSegmentId === thread.segment_id ? "is-active" : ""}" ${open ? "open" : ""}>
        <summary class="segment-followup-panel__summary">
          <span class="segment-followup-panel__badge">片段</span>
          <span class="segment-followup-panel__source-text">${escapeHtml(thread.original_text)}</span>
          <span class="segment-followup-panel__status segment-followup-panel__status--${escapeHtml(thread.status)}">${statusLabel(thread.status)}</span>
          <strong>${escapeHtml(lastTurn.question || "片段追问")}</strong>
          <small>${thread.turns.length} 轮对话</small>
          <button class="segment-followup-panel__expand" data-expand-followup data-message-id="${activeFollowupMessageId}" data-segment-id="${escapeHtml(thread.segment_id)}" title="放大片段追问" type="button">□</button>
        </summary>
        <div class="segment-followup-panel__answer">
          ${renderFollowupTurns(thread, false)}
        </div>
      </details>`;
  }

  function renderFollowupTurns(thread, showGraph) {
    if (!thread.turns.length) {
      return `<p class="segment-followup-panel__pending">这条追问还没有回复。</p>`;
    }
    return `<div class="segment-followup-thread-turns">${thread.turns.map((turn, index) => `
      <section class="${showGraph ? "segment-followup-modal__turn" : "segment-followup-turn"}">
        <p class="segment-followup-turn__question"><span>Q${index + 1}</span>${escapeHtml(turn.question)}</p>
        <div class="segment-followup-turn__answer">
          <div class="segment-followup-turn__assistant">
            <div class="markdown">${turn.answer_html || `<p class="segment-followup-panel__pending">这条追问还没有回复。</p>`}</div>
            ${showGraph && turn.graph_id ? `<button class="export-icon-button" data-open-graph data-graph-id="${turn.graph_id}" type="button" title="查看这轮追问 graph">G</button>` : ""}
          </div>
        </div>
      </section>`).join("")}</div>`;
  }

  function openFollowupModal(messageId, segmentId) {
    const thread = findThread(messageId, segmentId);
    if (!thread || !followupModalRoot) return;
    const lastTurn = thread.turns[thread.turns.length - 1] || {};
    followupModalRoot.innerHTML = `
      <div class="segment-followup-modal-backdrop" role="presentation" data-close-followup-modal>
        <section class="segment-followup-modal" role="dialog" aria-label="放大片段追问" aria-modal="true">
          <header class="segment-followup-modal__header">
            <div>
              <span>片段追问</span>
              <h3>${escapeHtml(lastTurn.question || "片段小会话")}</h3>
            </div>
            <button class="export-icon-button" data-close-followup-modal type="button" title="关闭">×</button>
          </header>
          <div class="segment-followup-modal__source">
            <span>片段</span>
            <q>${escapeHtml(thread.original_text)}</q>
          </div>
          <div class="segment-followup-modal__body">
            <div class="segment-followup-modal__turns">${renderFollowupTurns(thread, true)}</div>
          </div>
          <p class="segment-followup-modal__readonly">这是导出的只读 HTML，不能继续追问；请回到 AiMemo 原对话中继续。</p>
        </section>
      </div>`;
  }

  function closeFollowupModal() {
    if (followupModalRoot) followupModalRoot.innerHTML = "";
  }

  function openGraphDrawer(graphId) {
    if (!graphDrawerRoot || !graphId || !DATA.graphs?.[String(graphId)]) return;
    activeGraphId = String(graphId);
    activeGraphTab = "graph";
    activeGraphNode = null;
    renderGraphDrawer();
  }

  function closeGraphDrawer() {
    activeGraphId = null;
    activeGraphNode = null;
    if (graphDrawerRoot) graphDrawerRoot.innerHTML = "";
  }

  function renderGraphDrawer() {
    const graph = DATA.graphs?.[activeGraphId];
    if (!graphDrawerRoot || !graph) return;
    graphDrawerRoot.innerHTML = `
      <section class="chat-debug-workspace" role="dialog" aria-label="Graph 调试工作台" aria-modal="true">
        <header class="chat-debug-workspace-header">
          <div class="chat-debug-workspace-title">
            <h2>Graph 调试工作台</h2>
            <p>
              <span>turn #${escapeHtml(graph.turn_id)}</span>
              <span class="${graphStatusClass(graph.status)}">${escapeHtml(graph.status)}</span>
              ${graph.thread_id ? `<span>thread ${escapeHtml(graph.thread_id)}</span>` : ""}
            </p>
          </div>
          <nav class="chat-debug-workspace-tabs" role="tablist">
            ${renderGraphTabButton("graph", "图结构")}
            ${renderGraphTabButton("checkpoints", `Checkpoint 对比${graph.state_history?.states?.length ? ` · ${graph.state_history.states.length}` : ""}`)}
            ${renderGraphTabButton("context", "上下文金字塔")}
            ${renderGraphTabButton("performance", "性能 / 证据")}
          </nav>
          <button class="export-icon-button" data-close-graph type="button">收起</button>
        </header>
        <div class="chat-debug-workspace-body">${renderGraphTab(graph)}</div>
      </section>`;
  }

  function renderGraphTabButton(tab, label) {
    return `<button class="${activeGraphTab === tab ? "is-active" : ""}" data-graph-tab="${tab}" role="tab" type="button">${escapeHtml(label)}</button>`;
  }

  function renderGraphTab(graph) {
    if (activeGraphTab === "context") return renderContextTab(graph);
    if (activeGraphTab === "performance") return renderPerformanceTab(graph);
    if (activeGraphTab === "checkpoints") return renderCheckpointsTab(graph);
    return renderGraphStructureTab(graph);
  }

  function renderGraphStructureTab(graph) {
    const entries = Object.entries(graph.node_statuses || {});
    const selectedPayload = activeGraphNode
      ? graph.debug_payload?.nodes?.[activeGraphNode] || null
      : null;
    return `
      <div class="chat-debug-graph-tab">
        <div class="chat-debug-graph-canvas">
          <div class="export-graph-flow">
            ${entries.map(([node, status]) => `
              <button class="export-graph-node status-${escapeHtml(status)} ${activeGraphNode === node ? "is-active" : ""}" data-graph-node="${escapeHtml(node)}" type="button">
                <strong>${escapeHtml(node)}</strong>
                <em>${escapeHtml(status)}</em>
              </button>`).join("") || `<p class="chat-debug-empty">暂无节点状态。</p>`}
          </div>
        </div>
        <aside class="chat-debug-graph-aside">
          ${activeGraphNode ? `
            <section class="chat-debug-aside-section">
              <header>
                <h4>节点 state：${escapeHtml(activeGraphNode)}</h4>
                <button class="export-icon-button" data-clear-graph-node type="button">×</button>
              </header>
              ${renderJsonTree(selectedPayload || {})}
            </section>` : `<p class="chat-debug-empty">点击 graph 中的节点查看 state。</p>`}
          <section class="chat-debug-aside-section">
            <header><h4>Mermaid 源码</h4></header>
            <pre>${escapeHtml(graph.mermaid || "graph TD")}</pre>
          </section>
        </aside>
      </div>`;
  }

  function renderContextTab(graph) {
    const layers = graph.context_layers || [];
    if (!layers.length) return `<p class="chat-debug-empty">暂无上下文记录。</p>`;
    return `<div class="chat-debug-context-tab">${layers.map((layer) => `
      <details class="chat-debug-context-card" open>
        <summary>
          <span class="chat-debug-context-level chat-debug-context-level-${String(layer.level ?? 0).replace(".", "-")}">L${escapeHtml(layer.level ?? 0)}</span>
          <strong>${escapeHtml(layer.name || "context")}</strong>
          <small>${escapeHtml(layer.used_tokens ?? 0)} tokens${layer.budget_tokens ? ` / ${escapeHtml(layer.budget_tokens)}` : ""}</small>
        </summary>
        <pre>${escapeHtml(layer.content || layer.note || "空")}</pre>
      </details>`).join("")}</div>`;
  }

  function renderPerformanceTab(graph) {
    const summary = graph.debug_payload?.summary || {};
    const nodes = Object.entries(graph.debug_payload?.nodes || {});
    const chunks = graph.retrieved_chunks || [];
    return `
      <div class="chat-debug-performance-tab">
        <section class="chat-debug-performance-summary">
          <div><span>首 token</span><strong>${formatMs(summary.first_answer_token_ms)}</strong></div>
          <div><span>最后 token</span><strong>${formatMs(summary.last_answer_token_ms)}</strong></div>
          <div><span>token 事件</span><strong>${escapeHtml(summary.answer_token_events ?? 0)}</strong></div>
          <div><span>检索数</span><strong>${escapeHtml(summary.retrieved_count ?? chunks.length)}</strong></div>
        </section>
        ${nodes.length ? `
          <table class="chat-debug-performance-table">
            <thead><tr><th>节点</th><th>状态</th><th>开始</th><th>完成</th><th>耗时</th></tr></thead>
            <tbody>${nodes.map(([node, payload]) => `
              <tr>
                <td>${escapeHtml(node)}</td>
                <td>${escapeHtml(payload.status || "-")}</td>
                <td>${formatMs(payload.started_ms)}</td>
                <td>${formatMs(payload.completed_ms)}</td>
                <td>${formatMs(payload.duration_ms)}</td>
              </tr>`).join("")}</tbody>
          </table>` : ""}
        <h4>检索证据</h4>
        ${chunks.length ? chunks.map((chunk) => `
          <article class="chat-evidence-card">
            <strong>${escapeHtml(chunk.note_title || chunk.document_title || "检索证据")}</strong>
            <small>chunk #${escapeHtml(chunk.chunk_index ?? "-")} · score ${escapeHtml(formatScore(chunk.score))}</small>
            <p>${escapeHtml(chunk.content || "")}</p>
          </article>`).join("") : `<p class="chat-debug-empty">本轮没有可展示的检索结果。</p>`}
      </div>`;
  }

  function renderCheckpointsTab(graph) {
    const history = graph.state_history || {};
    const states = history.states || [];
    if (!states.length) {
      return `<div class="chat-debug-checkpoints-tab"><p class="chat-debug-empty">${escapeHtml(history.export_note || "导出文件未包含 checkpoint history。")}</p></div>`;
    }
    return `<div class="chat-debug-checkpoints-tab">${renderJsonTree(states)}</div>`;
  }

  function renderJsonTree(value, label = "root", depth = 0) {
    if (value === null) return `<span class="chat-debug-json-scalar">null</span>`;
    if (Array.isArray(value)) {
      return `<div class="chat-debug-json-tree"><details ${depth < 1 ? "open" : ""}><summary><span class="chat-debug-json-key">${escapeHtml(label)}</span>: Array · ${value.length}</summary>${value.map((item, index) => renderJsonTreeInner(item, String(index), depth + 1)).join("")}</details></div>`;
    }
    if (typeof value === "object") {
      const entries = Object.entries(value || {});
      return `<div class="chat-debug-json-tree"><details ${depth < 1 ? "open" : ""}><summary><span class="chat-debug-json-key">${escapeHtml(label)}</span>: Object · ${entries.length} keys</summary>${entries.map(([key, item]) => renderJsonTreeInner(item, key, depth + 1)).join("")}</details></div>`;
    }
    return `<span class="chat-debug-json-scalar">${escapeHtml(String(value))}</span>`;
  }

  function renderJsonTreeInner(value, label, depth) {
    if (value && typeof value === "object") {
      const entries = Array.isArray(value) ? value.map((item, index) => [String(index), item]) : Object.entries(value);
      return `<details ${depth < 2 ? "open" : ""} style="margin-left:${depth * 12}px"><summary><span class="chat-debug-json-key">${escapeHtml(label)}</span>: ${Array.isArray(value) ? `Array · ${value.length}` : `Object · ${entries.length} keys`}</summary>${entries.map(([key, item]) => renderJsonTreeInner(item, key, depth + 1)).join("")}</details>`;
    }
    const className = typeof value === "string" ? "chat-debug-json-string" : "chat-debug-json-scalar";
    return `<div style="margin-left:${depth * 12}px"><span class="chat-debug-json-key">${escapeHtml(label)}:</span> <span class="${className}">${escapeHtml(JSON.stringify(value))}</span></div>`;
  }

  function formatMs(value) {
    if (typeof value !== "number") return "-";
    return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${value}ms`;
  }

  function formatScore(value) {
    return typeof value === "number" ? value.toFixed(3) : "-";
  }

  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    const followupMark = target.closest(".segment-followup-mark");
    if (followupMark) {
      openFollowupPanel(followupMark.dataset.messageId, followupMark.dataset.segmentId);
      return;
    }
    const followupButton = target.closest("[data-open-followups]");
    if (followupButton) {
      openFollowupPanel(followupButton.dataset.messageId);
      return;
    }
    const closeFollowups = target.closest("[data-close-followups]");
    if (closeFollowups) {
      closeFollowupPanel();
      return;
    }
    const expandFollowup = target.closest("[data-expand-followup]");
    if (expandFollowup) {
      event.preventDefault();
      openFollowupModal(expandFollowup.dataset.messageId, expandFollowup.dataset.segmentId);
      return;
    }
    const closeFollowupModalTarget = target.closest("[data-close-followup-modal]");
    if (closeFollowupModalTarget) {
      const isBackdropClick =
        closeFollowupModalTarget.classList.contains("segment-followup-modal-backdrop") &&
        target === closeFollowupModalTarget;
      const isCloseButton = closeFollowupModalTarget.tagName === "BUTTON";
      if (!isBackdropClick && !isCloseButton) {
        // Clicks inside the modal body should keep bubbling to inner controls,
        // such as the graph button attached to a follow-up answer.
      } else {
        closeFollowupModal();
        return;
      }
    }
    const graphButton = target.closest("[data-open-graph]");
    if (graphButton) {
      openGraphDrawer(graphButton.dataset.graphId);
      return;
    }
    const closeGraph = target.closest("[data-close-graph]");
    if (closeGraph) {
      closeGraphDrawer();
      return;
    }
    const graphTab = target.closest("[data-graph-tab]");
    if (graphTab) {
      activeGraphTab = graphTab.dataset.graphTab || "graph";
      renderGraphDrawer();
      return;
    }
    const graphNode = target.closest("[data-graph-node]");
    if (graphNode) {
      activeGraphNode = graphNode.dataset.graphNode || null;
      renderGraphDrawer();
      return;
    }
    if (target.closest("[data-clear-graph-node]")) {
      activeGraphNode = null;
      renderGraphDrawer();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    closeFollowupModal();
    closeGraphDrawer();
    closeFollowupPanel();
  });

  markFollowupSegments();
})();
"""
