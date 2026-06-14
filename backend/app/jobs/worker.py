import logging
import threading
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
import time
from uuid import uuid4

from sqlmodel import Session

from app.core.config import settings
from app.core.database import session_scope
from app.jobs.handlers import JobHandler, build_job_handlers
from app.jobs.queue import claim_next_job, complete_job, fail_job, recover_stale_running_jobs
from app.schemas.elf import ElfEventCreate
from app.services.elf_event_service import emit_elf_event


logger = logging.getLogger(__name__)


class JobWorker:
    """用于 SQLite 持久化任务队列的小型本地 worker。

    worker 统一负责 job 生命周期流转。单个 handler 可以执行 LangGraph graph，
    但不应该直接把 job 标记为 completed/failed。
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractContextManager[Session]],
        handlers: Mapping[str, JobHandler],
        poll_interval_seconds: float,
        running_timeout_seconds: int,
        max_concurrency: int = 1,
        worker_id: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.handlers = handlers
        self.poll_interval_seconds = poll_interval_seconds
        self.running_timeout_seconds = running_timeout_seconds
        self.max_concurrency = max(1, int(max_concurrency))
        self.worker_id = worker_id or f"worker:{uuid4()}"
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._runner_threads: set[threading.Thread] = set()
        self._runner_lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.run_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        deadline = time.monotonic() + 5
        for runner in self._snapshot_runner_threads():
            remaining = max(0.0, deadline - time.monotonic())
            runner.join(timeout=remaining)

    def run_forever(self) -> None:
        while not self._stop_event.is_set():
            self._discard_finished_runners()
            started = False
            while not self._stop_event.is_set() and self._active_runner_count() < self.max_concurrency:
                job = self._claim_one_job()
                if job is None:
                    break
                self._start_runner(job)
                started = True
            if not started:
                self._stop_event.wait(self.poll_interval_seconds)

    def run_once(self) -> bool:
        job = self._claim_one_job()
        if job is None:
            return False
        self._execute_job(job)
        return True

    def _claim_one_job(self):
        with self.session_factory() as session:
            # 领取任务前先做恢复扫描，让中断过的 running job 能重新变成可领取状态。
            recover_stale_running_jobs(
                session,
                timeout_seconds=self.running_timeout_seconds,
            )
            return claim_next_job(session, worker_id=self.worker_id, max_running=self.max_concurrency)

    def _start_runner(self, job) -> None:
        runner = threading.Thread(target=self._run_and_unregister, args=(job,), daemon=True)
        with self._runner_lock:
            self._runner_threads.add(runner)
        runner.start()

    def _run_and_unregister(self, job) -> None:
        try:
            self._execute_job(job)
        finally:
            with self._runner_lock:
                self._runner_threads.discard(threading.current_thread())

    def _execute_job(self, job) -> None:
        handler = self.handlers.get(job.type)
        if handler is None:
            with self.session_factory() as session:
                attached_job = session.get(type(job), job.id)
                if attached_job:
                    fail_job(session, attached_job, f"No handler registered for job type {job.type}.")
            _emit_job_failed_event(job.id or 0, job.type, f"No handler registered for job type {job.type}.")
            return

        try:
            # handler 执行真正的工作。对于 graph-backed job，它会根据 job.thread_id
            # 从 checkpoint 恢复。
            handler(job)
        except Exception as exc:
            logger.exception("Job %s failed.", job.id)
            with self.session_factory() as session:
                attached_job = session.get(type(job), job.id)
                if attached_job:
                    fail_job(session, attached_job, str(exc))
            _emit_job_failed_event(job.id or 0, job.type, str(exc))
        else:
            with self.session_factory() as session:
                attached_job = session.get(type(job), job.id)
                if attached_job:
                    complete_job(session, attached_job)
            _emit_job_completed_event(job.id or 0, job.type)

    def _active_runner_count(self) -> int:
        with self._runner_lock:
            return sum(1 for runner in self._runner_threads if runner.is_alive())

    def _snapshot_runner_threads(self) -> list[threading.Thread]:
        with self._runner_lock:
            return list(self._runner_threads)

    def _discard_finished_runners(self) -> None:
        with self._runner_lock:
            self._runner_threads = {runner for runner in self._runner_threads if runner.is_alive()}


_worker: JobWorker | None = None


def start_job_worker() -> None:
    global _worker
    if not settings.job_worker_enabled:
        return
    if _worker is not None:
        return
    handlers = build_job_handlers(
        session_factory=session_scope,
        checkpoint_path=settings.langgraph_checkpoint_path,
    )
    _worker = JobWorker(
        session_factory=session_scope,
        handlers=handlers,
        poll_interval_seconds=settings.job_worker_poll_interval_seconds,
        running_timeout_seconds=settings.job_running_timeout_seconds,
        max_concurrency=settings.job_worker_concurrency,
    )
    _worker.start()


def stop_job_worker() -> None:
    global _worker
    if _worker is None:
        return
    _worker.stop()
    _worker = None


def _emit_job_completed_event(job_id: int, job_type: str) -> None:
    """把后台 job 完成转换成精灵事件。

    job 是后端最接近后台状态变化的地方，因此这里发布事件比前端轮询 jobs 后再猜测
    更准确，也能让桌面精灵在浏览器关闭时继续感知任务进度。
    """

    if job_type in {"conversation_summary", "conversation_memory"}:
        # 对话后的摘要/长期记忆抽取属于后台维护动作，不应该打断外置精灵聊天。
        # 这些状态仍可在 jobs/graph 调试面板里查看，后续如需展示应走低频调试通知。
        return

    emit_elf_event(
        ElfEventCreate(
            source="jobs",
            mood="success",
            motion="success",
            message=_job_completed_message(job_type),
            priority=45,
            ttl_ms=3600,
            dedupe_key=f"job:{job_id}:completed",
            metadata={"job_id": job_id, "job_type": job_type},
        )
    )


def _emit_job_failed_event(job_id: int, job_type: str, error: str) -> None:
    """把后台 job 失败转换成精灵事件。"""

    emit_elf_event(
        ElfEventCreate(
            source="jobs",
            mood="error",
            motion="error",
            message="有个后台任务失败了，我放到工坊里了。",
            priority=95,
            ttl_ms=7000,
            dedupe_key=f"job:{job_id}:failed",
            metadata={"job_id": job_id, "job_type": job_type, "error": error},
        )
    )


def _job_completed_message(job_type: str) -> str:
    if job_type == "note_metadata":
        return "我整理好了这条笔记的标题和标签。"
    if job_type == "note_embedding":
        return "这条笔记已经进入记忆库了。"
    if job_type == "knowledge_ingest":
        return "知库文档已经整理好了。"
    if job_type == "conversation_summary":
        return "我更新了这段对话的摘要。"
    if job_type == "conversation_memory":
        return "我从对话里提炼了一点长期记忆。"
    return "刚刚有个后台任务完成了。"
