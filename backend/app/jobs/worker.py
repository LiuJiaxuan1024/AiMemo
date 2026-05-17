import logging
import threading
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from uuid import uuid4

from sqlmodel import Session

from app.core.config import settings
from app.core.database import session_scope
from app.jobs.handlers import JobHandler, build_job_handlers
from app.jobs.queue import claim_next_job, complete_job, fail_job, recover_stale_running_jobs


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
        worker_id: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.handlers = handlers
        self.poll_interval_seconds = poll_interval_seconds
        self.running_timeout_seconds = running_timeout_seconds
        self.worker_id = worker_id or f"worker:{uuid4()}"
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.run_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def run_forever(self) -> None:
        while not self._stop_event.is_set():
            handled = self.run_once()
            if not handled:
                self._stop_event.wait(self.poll_interval_seconds)

    def run_once(self) -> bool:
        with self.session_factory() as session:
            # 领取任务前先做恢复扫描，让中断过的 running job 能重新变成可领取状态。
            recover_stale_running_jobs(
                session,
                timeout_seconds=self.running_timeout_seconds,
            )
            job = claim_next_job(session, worker_id=self.worker_id)
            if job is None:
                return False

        handler = self.handlers.get(job.type)
        if handler is None:
            with self.session_factory() as session:
                attached_job = session.get(type(job), job.id)
                if attached_job:
                    fail_job(session, attached_job, f"No handler registered for job type {job.type}.")
            return True

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
        else:
            with self.session_factory() as session:
                attached_job = session.get(type(job), job.id)
                if attached_job:
                    complete_job(session, attached_job)
        return True


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
    )
    _worker.start()


def stop_job_worker() -> None:
    global _worker
    if _worker is None:
        return
    _worker.stop()
    _worker = None
