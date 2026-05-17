from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from app.core.config import settings


def get_sqlite_checkpointer(path: str | None = None):
    # LangGraph checkpoint 是 graph 层的恢复机制。Job 告诉我们哪个任务要继续，
    # checkpointer 告诉 LangGraph 应该从哪个节点继续。
    checkpoint_path = Path(path or settings.langgraph_checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver.from_conn_string(str(checkpoint_path))
