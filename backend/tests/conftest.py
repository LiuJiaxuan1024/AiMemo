from collections.abc import Generator
from contextlib import contextmanager
import os
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import Job, Note


os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_CALLBACKS_BACKGROUND", "false")


@pytest.fixture
def test_engine(tmp_path: Path):
    database_path = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def session_factory(test_engine):
    @contextmanager
    def factory() -> Generator[Session, None, None]:
        with Session(test_engine) as session:
            yield session

    return factory


@pytest.fixture
def session(session_factory):
    with session_factory() as current_session:
        yield current_session
