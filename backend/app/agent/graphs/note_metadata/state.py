from typing import TypedDict


class NoteMetadataGraphState(TypedDict, total=False):
    # job_id/thread_id 标识外层持久化任务；note_id 标识 graph 允许修改的业务记录。
    job_id: int
    note_id: int
    # content 和 metadata 是 graph 内部的中间状态。generate_metadata 完成后，
    # checkpoint 会保存 metadata，恢复时可以直接进入 write_metadata，避免重复调用 LLM。
    content: str
    metadata: dict
