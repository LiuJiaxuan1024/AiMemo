from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from sqlmodel import Session

from app.agent.checkpoints import get_sqlite_checkpointer
from app.agent.graphs.note_metadata.nodes import (
    MetadataGenerator,
    build_generate_metadata_node,
    build_load_note_node,
    build_mark_failed_note,
    build_write_metadata_node,
)
from app.agent.graphs.note_metadata.state import NoteMetadataGraphState
from app.ai.note_metadata import generate_note_metadata
from app.jobs.payloads import decode_payload
from app.models.job import Job


SessionFactory = Callable[[], AbstractContextManager[Session]]


def build_note_metadata_graph(
    *,
    session_factory: SessionFactory,
    metadata_generator: MetadataGenerator = generate_note_metadata,
):
    # graph 构建保持纯粹：节点依赖都显式注入，测试可以替换 fake generator/session，
    # 恢复行为也更容易推理。
    graph = StateGraph(NoteMetadataGraphState)
    graph.add_node("load_note", build_load_note_node(session_factory))
    graph.add_node("generate_metadata", build_generate_metadata_node(metadata_generator))
    graph.add_node("write_metadata", build_write_metadata_node(session_factory))
    graph.add_edge(START, "load_note")
    graph.add_edge("load_note", "generate_metadata")
    graph.add_edge("generate_metadata", "write_metadata")
    graph.add_edge("write_metadata", END)
    return graph


def run_note_metadata_graph(
    job: Job,
    *,
    session_factory: SessionFactory,
    checkpoint_path: str,
    metadata_generator: MetadataGenerator = generate_note_metadata,
    interrupt_after: list[str] | None = None,
) -> None:
    payload = decode_payload(job.payload)
    note_id = int(payload["note_id"])
    # 一个 job 对应一个 LangGraph thread，避免不同任务或不同笔记的 checkpoint 混在一起。
    thread_id = job.thread_id or f"job:{job.id}"
    checkpoint_file = Path(checkpoint_path)
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)

    with get_sqlite_checkpointer(str(checkpoint_file)) as checkpointer:
        # 编译后的 graph 当前会绑定具体的 checkpointer 连接，所以这里在执行时编译。
        # graph 本身很小，真正昂贵且需要恢复的是节点里的 LLM/写库逻辑；
        # LangGraph 会在节点后写 checkpoint。
        app = build_note_metadata_graph(
            session_factory=session_factory,
            metadata_generator=metadata_generator,
        ).compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = app.get_state(config)
        # 如果 checkpoint 已经有 next 节点，传 None 让 LangGraph 从持久化状态继续；
        # 否则这是首次运行，需要传入初始 state。
        graph_input = None if snapshot.next else {"job_id": job.id or 0, "note_id": note_id}
        try:
            app.invoke(graph_input, config, interrupt_after=interrupt_after)
        except Exception as exc:
            build_mark_failed_note(session_factory)(job, str(exc))
            raise
