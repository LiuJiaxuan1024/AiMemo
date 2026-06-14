from dataclasses import dataclass

from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.database import session_scope
from app.jobs.models import GraphName
from app.jobs.payloads import decode_payload
from app.models.job import Job
from app.models.knowledge import KnowledgeImageAsset


@dataclass(frozen=True)
class JobGraphView:
    mermaid: str
    next_nodes: list[str]


def get_job_graph_view(job: Job, session: Session | None = None) -> JobGraphView:
    if job.graph_name == GraphName.NOTE_METADATA.value:
        return _get_note_metadata_graph_view(job)
    if job.graph_name == GraphName.NOTE_EMBEDDING.value:
        return _get_note_embedding_graph_view(job)
    if job.graph_name == GraphName.KNOWLEDGE_INGEST.value:
        return _get_knowledge_ingest_graph_view(job)
    if job.graph_name == GraphName.KNOWLEDGE_IMAGE_RETRY.value:
        return _get_knowledge_image_retry_graph_view(job, session=session)
    if job.graph_name == GraphName.CONVERSATION_SUMMARY.value:
        return _get_conversation_summary_graph_view(job)
    if job.graph_name == GraphName.CONVERSATION_MEMORY.value:
        return _get_conversation_memory_graph_view(job)
    raise ValueError(f"Unsupported graph: {job.graph_name}")


def _get_note_metadata_graph_view(job: Job) -> JobGraphView:
    from app.agent.checkpoints import get_sqlite_checkpointer
    from app.agent.graphs.note_metadata.graph import build_note_metadata_graph

    thread_id = job.thread_id or f"job:{job.id}"
    with get_sqlite_checkpointer(settings.langgraph_checkpoint_path) as checkpointer:
        app = build_note_metadata_graph(session_factory=session_scope).compile(
            checkpointer=checkpointer
        )
        snapshot = app.get_state({"configurable": {"thread_id": thread_id}})
        next_nodes = list(snapshot.next)
        mermaid = app.get_graph().draw_mermaid()

    return JobGraphView(
        mermaid=_highlight_nodes(mermaid, next_nodes),
        next_nodes=next_nodes,
    )


def _get_note_embedding_graph_view(job: Job) -> JobGraphView:
    from app.agent.checkpoints import get_sqlite_checkpointer
    from app.agent.graphs.note_embedding.graph import build_note_embedding_graph

    thread_id = job.thread_id or f"job:{job.id}"
    with get_sqlite_checkpointer(settings.langgraph_checkpoint_path) as checkpointer:
        app = build_note_embedding_graph(session_factory=session_scope).compile(
            checkpointer=checkpointer
        )
        snapshot = app.get_state({"configurable": {"thread_id": thread_id}})
        next_nodes = list(snapshot.next)
        mermaid = app.get_graph().draw_mermaid()

    return JobGraphView(
        mermaid=_highlight_nodes(mermaid, next_nodes),
        next_nodes=next_nodes,
    )


def _get_knowledge_ingest_graph_view(job: Job) -> JobGraphView:
    from app.agent.checkpoints import get_sqlite_checkpointer
    from app.agent.graphs.knowledge_ingest.graph import build_knowledge_ingest_graph

    thread_id = job.thread_id or f"job:{job.id}"
    with get_sqlite_checkpointer(settings.langgraph_checkpoint_path) as checkpointer:
        app = build_knowledge_ingest_graph(session_factory=session_scope).compile(
            checkpointer=checkpointer
        )
        snapshot = app.get_state({"configurable": {"thread_id": thread_id}})
        next_nodes = list(snapshot.next)
        mermaid = app.get_graph().draw_mermaid()

    return JobGraphView(
        mermaid=_highlight_nodes(mermaid, next_nodes),
        next_nodes=next_nodes,
    )


def _get_conversation_summary_graph_view(job: Job) -> JobGraphView:
    from app.agent.checkpoints import get_sqlite_checkpointer
    from app.agent.graphs.conversation_summary.graph import build_conversation_summary_graph

    thread_id = job.thread_id or f"job:{job.id}"
    with get_sqlite_checkpointer(settings.langgraph_checkpoint_path) as checkpointer:
        app = build_conversation_summary_graph(session_factory=session_scope).compile(
            checkpointer=checkpointer
        )
        snapshot = app.get_state({"configurable": {"thread_id": thread_id}})
        next_nodes = list(snapshot.next)
        mermaid = app.get_graph().draw_mermaid()

    return JobGraphView(
        mermaid=_highlight_nodes(mermaid, next_nodes),
        next_nodes=next_nodes,
    )


def _get_conversation_memory_graph_view(job: Job) -> JobGraphView:
    from app.agent.checkpoints import get_sqlite_checkpointer
    from app.agent.graphs.conversation_memory.graph import build_conversation_memory_graph

    thread_id = job.thread_id or f"job:{job.id}"
    with get_sqlite_checkpointer(settings.langgraph_checkpoint_path) as checkpointer:
        app = build_conversation_memory_graph(session_factory=session_scope).compile(
            checkpointer=checkpointer
        )
        snapshot = app.get_state({"configurable": {"thread_id": thread_id}})
        next_nodes = list(snapshot.next)
        mermaid = app.get_graph().draw_mermaid()

    return JobGraphView(
        mermaid=_highlight_nodes(mermaid, next_nodes),
        next_nodes=next_nodes,
    )


def _get_knowledge_image_retry_graph_view(job: Job, session: Session | None = None) -> JobGraphView:
    payload = decode_payload(job.payload)
    document_id = _payload_int(payload.get("document_id"))
    image_asset_ids = [_payload_int(item) for item in payload.get("image_asset_ids", [])]
    image_asset_ids = [item for item in image_asset_ids if item is not None]
    assets = _load_image_assets(session=session, image_asset_ids=image_asset_ids)
    next_nodes = _knowledge_image_retry_next_nodes(job)
    mermaid = _build_knowledge_image_retry_mermaid(
        job=job,
        document_id=document_id,
        image_asset_ids=image_asset_ids,
        assets=assets,
        next_nodes=next_nodes,
    )
    return JobGraphView(mermaid=mermaid, next_nodes=next_nodes)


def _knowledge_image_retry_next_nodes(job: Job) -> list[str]:
    if job.status == "pending":
        return ["load_document"]
    if job.status == "running":
        return ["retry_image_assets"]
    if job.status == "failed":
        return ["job_failed"]
    return []


def _load_image_assets(
    *,
    session: Session | None,
    image_asset_ids: list[int],
) -> list[KnowledgeImageAsset]:
    if not image_asset_ids:
        return []

    def query(current_session: Session) -> list[KnowledgeImageAsset]:
        return current_session.exec(
            select(KnowledgeImageAsset)
            .where(col(KnowledgeImageAsset.id).in_(image_asset_ids))
            .order_by(KnowledgeImageAsset.page_number, KnowledgeImageAsset.source_offset, KnowledgeImageAsset.id)
        ).all()

    if session is not None:
        return query(session)
    with session_scope() as current_session:
        return query(current_session)


def _build_knowledge_image_retry_mermaid(
    *,
    job: Job,
    document_id: int | None,
    image_asset_ids: list[int],
    assets: list[KnowledgeImageAsset],
    next_nodes: list[str],
) -> str:
    asset_by_id = {asset.id: asset for asset in assets if asset.id is not None}
    selected_assets = [asset_by_id.get(asset_id) for asset_id in image_asset_ids]
    lines = [
        "flowchart TD",
        "    start([start])",
        f"    load_document[\"load_document<br/>document #{document_id or '-'}\"]",
        "    parse_source[\"parse_source<br/>读取原始文档\"]",
        f"    match_assets[\"match_retry_assets<br/>{len(image_asset_ids)} selected\"]",
        "    retry_image_assets[\"retry_image_assets<br/>逐张 OCR / 入库\"]",
        "    refresh_stats[\"refresh_document_stats<br/>刷新文档统计\"]",
        "    done([end])",
        "    start --> load_document --> parse_source --> match_assets --> retry_image_assets",
    ]
    if not image_asset_ids:
        lines.append("    retry_image_assets --> refresh_stats")
    for index, asset_id in enumerate(image_asset_ids, start=1):
        asset = selected_assets[index - 1]
        node_id = f"asset_{asset_id}"
        lines.append(f"    retry_image_assets --> {node_id}[\"{_image_asset_node_label(asset_id, asset, index)}\"]")
        lines.append(f"    {node_id} --> refresh_stats")
    lines.append("    refresh_stats --> done")
    if job.status == "failed" or job.error:
        lines.append(f"    retry_image_assets --> job_failed[\"job_failed<br/>{_mermaid_label(job.error or 'Internal Server Error')}\"]")

    lines.extend(
        [
            "    classDef pending fill:#fff7ed,stroke:#fb923c,stroke-width:2px,color:#9a3412;",
            "    classDef processing fill:#eff6ff,stroke:#60a5fa,stroke-width:2px,color:#1d4ed8;",
            "    classDef completed fill:#ecfdf5,stroke:#34d399,stroke-width:2px,color:#047857;",
            "    classDef failed fill:#fef2f2,stroke:#f87171,stroke-width:2px,color:#b91c1c;",
            "    classDef skipped fill:#f3f4f6,stroke:#9ca3af,stroke-width:2px,color:#4b5563;",
            "    classDef activeJobNode fill:#fff7ed,stroke:#f97316,stroke-width:3px,color:#9a3412;",
        ]
    )
    for asset_id in image_asset_ids:
        asset = asset_by_id.get(asset_id)
        lines.append(f"    class asset_{asset_id} {_image_asset_status_class(asset)};")
    for node_name in next_nodes:
        lines.append(f"    class {node_name} activeJobNode;")
    if job.status == "failed" or job.error:
        lines.append("    class job_failed failed;")
    return "\n".join(lines)


def _image_asset_node_label(asset_id: int, asset: KnowledgeImageAsset | None, index: int) -> str:
    if asset is None:
        return _mermaid_label(f"asset #{asset_id}<br/>missing")
    parts = [
        f"asset #{asset.id or asset_id}",
        asset.location_label or asset.asset_id or f"image {index}",
        f"status: {asset.status}",
    ]
    if asset.attempt_count:
        parts.append(f"attempts: {asset.attempt_count}")
    if asset.error_code:
        parts.append(asset.error_code)
    return _mermaid_label("<br/>".join(parts))


def _image_asset_status_class(asset: KnowledgeImageAsset | None) -> str:
    if asset is None:
        return "failed"
    if asset.status in {"completed", "failed", "processing", "pending", "skipped"}:
        return asset.status
    return "skipped"


def _payload_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _mermaid_label(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', "'")
        .replace("[", "(")
        .replace("]", ")")
        .replace("\r", " ")
        .replace("\n", "<br/>")[:900]
    )


def _highlight_nodes(mermaid: str, node_names: list[str]) -> str:
    if not node_names:
        return mermaid

    lines = [
        mermaid.rstrip(),
        "classDef activeJobNode fill:#fff7ed,stroke:#f97316,stroke-width:3px,color:#9a3412;",
    ]
    for node_name in node_names:
        lines.append(f"class {node_name} activeJobNode;")
    return "\n".join(lines)
