"""FastAPI application factory + wiring."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from backend.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    from backend.database.session import SessionLocal, init_db
    from backend.prompts.templates import seed_prompt_templates
    from backend.api.v1.auth import ensure_bootstrap_admin

    init_db()
    db = SessionLocal()
    try:
        seed_prompt_templates(db)
        ensure_bootstrap_admin(db)
    finally:
        db.close()
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, version="0.2.0-phase2", lifespan=lifespan)
    if s.environment == "production":
        # reject hosts not explicitly allowed (design doc 06 §3)
        app.add_middleware(TrustedHostMiddleware,
                           allowed_hosts=["*.internal.university", "localhost", "127.0.0.1"])
        if s.secret_key.startswith("DEV-ONLY"):
            raise RuntimeError("refusing to start production with the dev secret key")

    from backend.api.v1 import auth, documents, jobs, questions, subjects
    app.include_router(auth.router, prefix=s.api_prefix)
    app.include_router(subjects.router, prefix=s.api_prefix)
    app.include_router(documents.router, prefix=s.api_prefix)
    app.include_router(jobs.router, prefix=s.api_prefix)
    app.include_router(questions.router, prefix=s.api_prefix)

    @app.get("/healthz", tags=["ops"])
    def healthz():
        return {"status": "ok", "app": s.app_name}

    return app


app = create_app()
