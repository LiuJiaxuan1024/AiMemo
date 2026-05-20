from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.elf import router as elf_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.memories import router as memories_router
from app.api.notes import router as notes_router
from app.api.search import router as search_router
from app.agent.model import warmup_agent_models
from app.core.config import settings
from app.core.database import create_db_and_tables
from app.frontend import mount_frontend_app
from app.jobs.reconciler import run_job_reconcile_once, start_job_reconciler, stop_job_reconciler
from app.jobs.worker import start_job_worker, stop_job_worker


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
    app.include_router(conversations_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(elf_router, prefix="/api")
    app.include_router(notes_router, prefix="/api")
    app.include_router(memories_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")
    app.include_router(search_router, prefix="/api")
    mount_frontend_app(app)

    @app.on_event("startup")
    def on_startup() -> None:
        create_db_and_tables()
        warmup_agent_models()
        if settings.job_reconciler_enabled:
            run_job_reconcile_once()
            start_job_reconciler()
        start_job_worker()

    @app.on_event("shutdown")
    def on_shutdown() -> None:
        stop_job_reconciler()
        stop_job_worker()

    return app


app = create_app()
