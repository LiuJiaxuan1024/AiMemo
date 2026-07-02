import os
import threading
import time
from collections.abc import Callable
from typing import TypeVar

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.app_config import router as app_config_router
from app.api.attachments import router as attachments_router
from app.api.background_tasks import router as background_tasks_router
from app.api.chat import router as chat_router
from app.api.cloud_sync import router as cloud_sync_router
from app.api.commands import router as commands_router
from app.api.conversations import router as conversations_router
from app.api.elf import router as elf_router
from app.api.elf_voice import router as elf_voice_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.knowledge import router as knowledge_router
from app.api.memories import router as memories_router
from app.api.note_categories import router as note_categories_router
from app.api.note_tags import router as note_tags_router
from app.api.notes import router as notes_router
from app.api.search import router as search_router
from app.api.voice_profiles import router as voice_profiles_router
from app.core.config import settings
from app.core.database import create_db_and_tables
from app.frontend import mount_frontend_app
from app.jobs.reconciler import run_job_reconcile_once, start_job_reconciler, stop_job_reconciler
from app.jobs.worker import start_job_worker, stop_job_worker
from app.local_operator.background_command import pool as background_shell_pool


_agent_model_warmup_thread: threading.Thread | None = None
_T = TypeVar("_T")


def _startup_profile_enabled() -> bool:
    return os.getenv("AIMEMO_PROFILE_STARTUP", "").lower() in {"1", "true", "yes", "on"}


def _profile_step(label: str, action: Callable[[], _T]) -> _T:
    if not _startup_profile_enabled():
        return action()

    started = time.perf_counter()
    print(f"[backend-startup] begin {label}", flush=True)
    try:
        return action()
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        print(f"[backend-startup] end {label} ({elapsed_ms:.1f} ms)", flush=True)


def create_app() -> FastAPI:
    create_started = time.perf_counter()
    app = FastAPI(title="Ai Ji API", version="0.1.0")

    _profile_step(
        "add CORS middleware",
        lambda: app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.resolved_cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    )

    for label, router in [
        ("health router", health_router),
        ("app_config router", app_config_router),
        ("attachments router", attachments_router),
        ("conversations router", conversations_router),
        ("chat router", chat_router),
        ("cloud_sync router", cloud_sync_router),
        ("commands router", commands_router),
        ("elf router", elf_router),
        ("elf_voice router", elf_voice_router),
        ("notes router", notes_router),
        ("note_categories router", note_categories_router),
        ("note_tags router", note_tags_router),
        ("knowledge router", knowledge_router),
        ("memories router", memories_router),
        ("jobs router", jobs_router),
        ("search router", search_router),
        ("background_tasks router", background_tasks_router),
        ("voice_profiles router", voice_profiles_router),
    ]:
        _profile_step(f"include {label}", lambda router=router: app.include_router(router, prefix="/api"))
    _profile_step("mount frontend app", lambda: mount_frontend_app(app))
    if _startup_profile_enabled():
        elapsed_ms = (time.perf_counter() - create_started) * 1000
        print(f"[backend-startup] create_app completed ({elapsed_ms:.1f} ms)", flush=True)

    @app.on_event("startup")
    def on_startup() -> None:
        startup_started = time.perf_counter()
        _profile_step("create_db_and_tables", create_db_and_tables)
        if settings.agent_model_warmup_enabled:
            from app.agent.model import warmup_agent_models

            _profile_step("agent model warmup", warmup_agent_models)
        elif settings.agent_model_background_warmup_enabled:
            _profile_step("start background agent model warmup", _start_agent_model_background_warmup)
        if settings.job_reconciler_enabled:
            _profile_step("run job reconcile once", run_job_reconcile_once)
            _profile_step("start job reconciler", start_job_reconciler)
        _profile_step("start job worker", start_job_worker)
        # 找回上次后端运行留下的后台进程：还活着的 re-register 到内存池，
        # 已退出的标记为 orphaned。让 UI 重启后仍能看到这些任务。
        try:
            stats = _profile_step("adopt persisted background tasks", background_shell_pool.adopt_persisted_tasks)
            print(f"[background_shell] adopted {stats.get('adopted', 0)}, orphaned {stats.get('orphaned', 0)}")
        except Exception as exc:
            print(f"[background_shell] adopt skipped: {exc}")
        # 收尾上次留下来的已终止任务：清掉 DB 行和 stdout/stderr 日志文件，
        # 避免列表里长期堆着 exited / killed / orphaned 的历史记录。仍在跑的任务不动。
        if settings.background_task_cleanup_on_startup:
            try:
                cleanup = _profile_step("cleanup finished background tasks", background_shell_pool.cleanup_finished_tasks)
                print(
                    "[background_shell] cleaned "
                    f"{cleanup.get('removed', 0)} finished tasks, "
                    f"deleted {cleanup.get('logs_deleted', 0)} log files"
                )
            except Exception as exc:
                print(f"[background_shell] cleanup skipped: {exc}")
        if _startup_profile_enabled():
            elapsed_ms = (time.perf_counter() - startup_started) * 1000
            print(f"[backend-startup] FastAPI startup completed ({elapsed_ms:.1f} ms)", flush=True)

    @app.on_event("shutdown")
    def on_shutdown() -> None:
        stop_job_reconciler()
        stop_job_worker()
        # 不再杀后台进程——它们是 detached 的，会继续运行。
        # 这里只关闭日志文件句柄；下次启动 adopt_persisted_tasks 会把它们找回来。
        background_shell_pool.shutdown_all()

    return app


def _start_agent_model_background_warmup() -> None:
    """后台预热 agent 模型，避免阻塞 FastAPI startup。"""

    global _agent_model_warmup_thread
    if _agent_model_warmup_thread and _agent_model_warmup_thread.is_alive():
        return

    def warmup() -> None:
        from app.agent.model import warmup_agent_models

        warmup_agent_models()

    _agent_model_warmup_thread = threading.Thread(
        target=warmup,
        name="agent-model-warmup",
        daemon=True,
    )
    _agent_model_warmup_thread.start()


app = create_app()
