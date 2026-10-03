"""AI-QBE test configuration.

Tests run against a temporary SQLite file (never the dev DB) and the mock LLM
provider — no GPU or network required. Environment variables are set BEFORE any
backend module is imported because Settings is lru_cached at import time.
"""
from __future__ import annotations

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="aiqbe_test_"), "test.db")
os.environ["AIQBE_DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["AIQBE_ENVIRONMENT"] = "development"
os.environ["AIQBE_LLM_PROVIDER"] = "mock"
os.environ["AIQBE_QUEUE_ENABLED"] = "false"
os.environ["AIQBE_BOOTSTRAP_ADMIN_PASSWORD"] = ""   # tests create users explicitly

import pytest                         # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def app():
    from backend.api.main import create_app
    return create_app()


@pytest.fixture(scope="session")
def client(app):
    # with TestClient as ctx, the lifespan runs: init_db + prompt seeding
    with TestClient(app) as ctx:
        yield ctx


@pytest.fixture(autouse=True)
def isolated_vector_store(tmp_path, monkeypatch):
    """Each test gets its own vector-index directory.

    Without this, tests sharing one on-disk index leak chunks into each other's
    retrieval results (order-dependent failures). The store cache is keyed by
    location, so pointing data_dir at a fresh tmp dir fully isolates every test.
    """
    from backend.core.config import get_settings
    from backend.rag.vectorstore import reset_vector_store

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    reset_vector_store()
    yield
    reset_vector_store()


@pytest.fixture()
def db_session():
    """Fresh schema per test.

    Tests share one session-scoped SQLite file (the app's own engine). The
    previous design leaked rows across tests: SourceDocument.file_hash is a
    GLOBAL uniqueness key, so an earlier test ingesting bytes X made a later
    test ingest the same bytes X report deduplicated=True against another
    test's chunks — order-dependent failures that passed in isolation. Rolling
    back cannot fix cross-connection visibility either (each connection only
    sees its own uncommitted rows), so the only sound isolation is to drop and
    recreate the schema around every test.
    """
    from backend.database.session import SessionLocal, init_db
    from sqlalchemy import text as sa_text

    engine = SessionLocal.kw["bind"] if hasattr(SessionLocal, "kw") else None
    init_db()   # idempotent create_all; unit tests may run without the client
    if engine is not None and engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            for tbl in reversed(_all_tables()):
                conn.execute(sa_text(f"DROP TABLE IF EXISTS {tbl.name}"))
    elif engine is not None:  # pragma: no cover - postgres dev parity
        with engine.begin() as conn:
            conn.execute(sa_text("TRUNCATE SCHEMA public RESTART IDENTITY CASCADE"))
    init_db()
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def topic_row(db_session):
    """A subject + topic row for RAG/retrieval tests (shared across modules)."""
    from backend.models.entities import Subject, Topic

    subj = Subject(name="TestSubject-ingest", code="TST-ING")
    db_session.add(subj)
    db_session.flush()
    t = Topic(subject_id=subj.id, name="Newton's Laws", importance=5)
    db_session.add(t)
    db_session.commit()
    return t


def _all_tables():
    from backend.models.entities import Base
    return list(Base.metadata.sorted_tables)


def make_user(username: str, role: str, password: str = "pw-123456") -> None:
    """Create (or replace) a user directly in the test DB."""
    from backend.core.security import hash_password
    from backend.database.session import SessionLocal
    from backend.models.entities import User

    s = SessionLocal()
    try:
        existing = s.query(User).filter_by(username=username).first()
        if existing:
            s.delete(existing)
            s.flush()
        s.add(User(username=username, password_hash=hash_password(password), role=role))
        s.commit()
    finally:
        s.close()


def token_for(client: TestClient, username: str, password: str = "pw-123456") -> str:
    r = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth(client: TestClient, role: str) -> dict:
    username = role.lower()
    make_user(username, role)
    return {"Authorization": f"Bearer {token_for(client, username)}"}


@pytest.fixture(scope="session")
def demo_subject(db_session):
    """Ensure Physics-I subject+topics exist; return (subject_id, [topic_ids])."""
    from backend.scripts.import_seed import seed_academic

    seed_academic(db_session)
    from backend.models.entities import Subject, Topic

    subj = db_session.query(Subject).filter_by(name="Physics-I").one()
    tids = [t.id for t in db_session.query(Topic).filter_by(subject_id=subj.id).all()]
    return subj.id, tids
