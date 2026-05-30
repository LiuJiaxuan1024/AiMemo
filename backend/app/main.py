from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.app_config import router as app_config_router
from app.api.background_tasks import router as background_tasks_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.elf import router as elf_router
from app.api.elf_voice import router as elf_voice_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.memories import router as memories_router
from app.api.notes import router as notes_router
from app.api.search import router as search_router
from app.api.voice_profiles import router as voice_profiles_router
from app.agent.model import warmup_agent_models
from app.core.config import settings
from app.core.database import create_db_and_tables
from app.frontend import mount_frontend_app
from app.jobs.reconciler import run_job_reconcile_once, start_job_reconciler, stop_job_reconciler
from app.jobs.worker import start_job_worker, stop_job_worker
from app.local_operator.background_command import pool as background_shell_pool


def create_app() -> FastAPI:
    app = FastAPI(title="Ai Ji API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix="/api")
    app.include_router(app_config_router, prefix="/api")
    app.include_router(conversations_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(elf_router, prefix="/api")
    app.include_router(elf_voice_router, prefix="/api")
    app.include_router(notes_router, prefix="/api")
    app.include_router(memories_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")
    app.include_router(search_router, prefix="/api")
    app.include_router(background_tasks_router, prefix="/api")
    app.include_router(voice_profiles_router, prefix="/api")
    mount_frontend_app(app)

    @app.on_event("startup")
    def on_startup() -> None:
        create_db_and_tables()
        warmup_agent_models()
        if settings.job_reconciler_enabled:
            run_job_reconcile_once()
            start_job_reconciler()
        start_job_worker()
        # 找回上次后端运行留下的后台进程：还活着的 re-register 到内存池，
        # 已退出的标记为 orphaned。让 UI 重启后仍能看到这些任务。
        try:
            stats = background_shell_pool.adopt_persisted_tasks()
            print(f"[background_shell] adopted {stats.get('adopted', 0)}, orphaned {stats.get('orphaned', 0)}")
        except Exception as exc:
            print(f"[background_shell] adopt skipped: {exc}")
        # 收尾上次留下来的已终止任务：清掉 DB 行和 stdout/stderr 日志文件，
        # 避免列表里长期堆着 exited / killed / orphaned 的历史记录。仍在跑的任务不动。
        if settings.background_task_cleanup_on_startup:
            try:
                cleanup = background_shell_pool.cleanup_finished_tasks()
                print(
                    "[background_shell] cleaned "
                    f"{cleanup.get('removed', 0)} finished tasks, "
                    f"deleted {cleanup.get('logs_deleted', 0)} log files"
                )
            except Exception as exc:
                print(f"[background_shell] cleanup skipped: {exc}")

    @app.on_event("shutdown")
    def on_shutdown() -> None:
        stop_job_reconciler()
        stop_job_worker()
        # 不再杀后台进程——它们是 detached 的，会继续运行。
        # 这里只关闭日志文件句柄；下次启动 adopt_persisted_tasks 会把它们找回来。
        background_shell_pool.shutdown_all()

    return app


app = create_app()
