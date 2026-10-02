"""AI-QBE configuration (pydantic-settings).

All deployment-specific values come from environment variables / .env file.
Defaults are development-safe; production startup asserts secrets are changed.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AIQBE_", extra="ignore")

    # --- general -------------------------------------------------------
    app_name: str = "AI-QBE"
    environment: str = "development"          # development | production
    api_prefix: str = "/api/v1"
    page_size_default: int = 50
    page_size_max: int = 200

    # --- database ------------------------------------------------------
    database_url: str = "sqlite:///./aiqbe_dev.db"   # prod: postgresql+psycopg://...
    db_echo: bool = False

    # --- auth ----------------------------------------------------------
    secret_key: str = "DEV-ONLY-INSECURE-CHANGE-ME"
    access_token_ttl_minutes: int = 60
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = ""                # empty => no bootstrap admin

    # --- job limits (FR-1 / FR-7) --------------------------------------
    max_questions_per_job: int = 3000
    overgeneration_factor_default: float = 1.35
    generation_batch_size: int = 10                    # never thousands per request
    max_attempts_multiplier: int = 2                   # stop after factor*target attempts

    # --- queue ---------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    queue_enabled: bool = False                        # MVP in-process runner when false

    # --- LLM -----------------------------------------------------------
    llm_provider: str = "mock"                         # mock | llamacpp | ollama | openai_compat
    llamacpp_base_url: str = "http://127.0.0.1:8080"
    ollama_base_url: str = "http://127.0.0.1:11434"
    openai_compat_base_url: str = "http://127.0.0.1:8000/v1"
    llm_timeout_seconds: float = 300.0

    # --- retrieval policy (Phase 4 wires enforcement) ------------------
    internet_retrieval_enabled: bool = False
    domain_policy_path: str = "deployment/domain_policy.yaml"

    # --- RAG / embeddings / vector store (Phase 3) ---------------------
    data_dir: str = "./data"
    embedding_provider: str = "hashed_ngram"           # hashed_ngram | sentence_transformers
    embedding_model: str = "BAAI/bge-small-en-v1.5"    # used when provider=ST
    embedding_dim: int = 512                           # hashed embedder dimension
    vector_store: str = "local_json"                   # local_json | qdrant
    qdrant_url: str = "http://127.0.0.1:6333"
    chunk_target_tokens: int = 300
    chunk_overlap_tokens: int = 40
    retrieval_top_k: int = 8
    retrieval_min_score: float = 0.05                  # tuned in Phase-3 eval
    max_upload_mb: int = 50

    # --- logging --------------------------------------------------------
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
