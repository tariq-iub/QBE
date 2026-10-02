"""AI-QBE ORM models — Phase 2 core subset of design doc 03.

Implemented here: users, academic_cache (subjects/topics/subtopics),
generation_models, prompt_templates, generation_jobs, generation_job_topics,
mcq_candidates, mcq_options, mcq_validation_results, mcq_sources, mcq_reviews,
academic_sources/source_documents/document_chunks (schema only for Phase 3).

Postgres-specific types are avoided in the ORM layer (JSON generic + String
enums) so dev SQLite works; the Alembic migration targets Postgres with native
JSONB/ENUMs. Provenance columns on mcq_candidates are NOT NULL (NFR-6).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.session import Base
from backend.models.enums import MCQStatus


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------- auth/users
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(sa.String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(sa.String(128))
    role: Mapped[str] = mapped_column(sa.String(32), default="ReadOnly")
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(sa.DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(sa.String(64))
    entity: Mapped[str] = mapped_column(sa.String(64))
    entity_id: Mapped[str] = mapped_column(sa.String(64))
    detail_json: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[dt.datetime] = mapped_column(sa.DateTime, default=utcnow, index=True)


# ------------------------------------------------------------- academic cache
class Subject(Base):
    """Read-through cache row for a legacy subject (never written upstream)."""
    __tablename__ = "subjects"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    program: Mapped[str | None] = mapped_column(sa.String(128))
    semester: Mapped[str | None] = mapped_column(sa.String(64))
    name: Mapped[str] = mapped_column(sa.String(255))
    code: Mapped[str | None] = mapped_column(sa.String(64))
    synced_at: Mapped[dt.datetime] = mapped_column(sa.DateTime, default=utcnow)
    topics: Mapped[list["Topic"]] = relationship(back_populates="subject")


class Topic(Base):
    __tablename__ = "topics"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(sa.ForeignKey("subjects.id"), index=True)
    external_id: Mapped[str | None] = mapped_column(sa.String(64))
    name: Mapped[str] = mapped_column(sa.String(255))
    teaching_hours: Mapped[float | None] = mapped_column(sa.Float)
    weight: Mapped[float | None] = mapped_column(sa.Float)
    importance: Mapped[int] = mapped_column(sa.Integer, default=3)  # 1..5 admin override
    subject: Mapped[Subject] = relationship(back_populates="topics")
    subtopics: Mapped[list["SubTopic"]] = relationship(back_populates="topic")


class SubTopic(Base):
    __tablename__ = "subtopics"
    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(sa.ForeignKey("topics.id"), index=True)
    external_id: Mapped[str | None] = mapped_column(sa.String(64))
    name: Mapped[str] = mapped_column(sa.String(255))
    topic: Mapped[Topic] = relationship(back_populates="subtopics")


# ------------------------------------------------------------ model registry
class GenerationModel(Base):
    __tablename__ = "generation_models"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(128))
    quantization: Mapped[str | None] = mapped_column(sa.String(32))
    version_hash: Mapped[str | None] = mapped_column(sa.String(128))
    provider_kind: Mapped[str] = mapped_column(sa.String(32))
    params_default_json: Mapped[str | None] = mapped_column(sa.Text)
    active: Mapped[bool] = mapped_column(sa.Boolean, default=True)


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(sa.String(64))           # mcq_generation_v1 ...
    version: Mapped[int] = mapped_column(sa.Integer, default=1)
    template_text: Mapped[str] = mapped_column(sa.Text)
    temperature: Mapped[Decimal] = mapped_column(sa.Numeric(3, 2), default=Decimal("0.30"))
    max_tokens: Mapped[int] = mapped_column(sa.Integer, default=1500)
    __table_args__ = (sa.UniqueConstraint("kind", "version"),)


# --------------------------------------------------------------------- jobs
class JobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(255))
    subject_id: Mapped[int] = mapped_column(sa.ForeignKey("subjects.id"), index=True)
    status: Mapped[str] = mapped_column(sa.String(16), default=JobStatus.QUEUED, index=True)
    target_approved: Mapped[int] = mapped_column(sa.Integer)
    num_options: Mapped[int] = mapped_column(sa.Integer, default=4)
    overgeneration_factor: Mapped[float] = mapped_column(sa.Float, default=1.35)
    bloom_dist_json: Mapped[str] = mapped_column(sa.Text)       # {"remember":0.25,...}
    difficulty_dist_json: Mapped[str] = mapped_column(sa.Text)
    retrieval_enabled: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    model_id: Mapped[int | None] = mapped_column(sa.ForeignKey("generation_models.id"))
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(128), unique=True)
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(sa.DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(sa.DateTime, default=utcnow, onupdate=utcnow)
    topics: Mapped[list["GenerationJobTopic"]] = relationship(
        back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (
        sa.CheckConstraint("target_approved > 0", name="ck_target_positive"),
    )


class GenerationJobTopic(Base):
    __tablename__ = "generation_job_topics"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(sa.ForeignKey("generation_jobs.id"), index=True)
    topic_id: Mapped[int] = mapped_column(sa.ForeignKey("topics.id"))
    weight: Mapped[float | None] = mapped_column(sa.Float)
    bloom_override_json: Mapped[str | None] = mapped_column(sa.Text)
    job: Mapped[GenerationJob] = relationship(back_populates="topics")
    __table_args__ = (sa.UniqueConstraint("job_id", "topic_id"),)


class JobCheckpoint(Base):
    """Resumable progress markers (design ADR-005 / FR-9)."""
    __tablename__ = "job_checkpoints"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(sa.ForeignKey("generation_jobs.id"), index=True)
    stage: Mapped[str] = mapped_column(sa.String(32))
    batch_cursor: Mapped[int] = mapped_column(sa.Integer, default=0)
    state_json: Mapped[str | None] = mapped_column(sa.Text)
    updated_at: Mapped[dt.datetime] = mapped_column(sa.DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (sa.UniqueConstraint("job_id", "stage"),)


# --------------------------------------------------------------- candidates
class MCQCandidate(Base):
    __tablename__ = "mcq_candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(sa.ForeignKey("generation_jobs.id"), index=True)
    topic_id: Mapped[int] = mapped_column(sa.ForeignKey("topics.id"), index=True)
    subtopic_id: Mapped[int | None] = mapped_column(sa.ForeignKey("subtopics.id"))
    stem: Mapped[str] = mapped_column(sa.Text)
    stem_norm: Mapped[str] = mapped_column(sa.String(1024), index=True)
    explanation: Mapped[str | None] = mapped_column(sa.Text)
    bloom_level: Mapped[str] = mapped_column(sa.String(16))
    difficulty: Mapped[str] = mapped_column(sa.String(8))
    question_type: Mapped[str | None] = mapped_column(sa.String(32))
    status: Mapped[str] = mapped_column(sa.String(24), default=MCQStatus.GENERATED)
    quality_score: Mapped[int | None] = mapped_column(sa.Integer)
    confidence: Mapped[Decimal | None] = mapped_column(sa.Numeric(3, 2))
    contains_latex: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    # ---- provenance (NOT NULL, NFR-6) ----
    model_id: Mapped[int] = mapped_column(sa.ForeignKey("generation_models.id"))
    prompt_template_id: Mapped[int] = mapped_column(sa.ForeignKey("prompt_templates.id"))
    generation_params_json: Mapped[str] = mapped_column(sa.Text)
    seed: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[dt.datetime] = mapped_column(sa.DateTime, default=utcnow)

    options: Mapped[list["MCQOption"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", order_by="MCQOption.letter")
    validation_results: Mapped[list["MCQValidationResult"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan")
    sources: Mapped[list["MCQSource"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan")
    reviews: Mapped[list["MCQReview"]] = relationship(back_populates="candidate")

    __table_args__ = (
        sa.CheckConstraint("quality_score IS NULL OR (quality_score BETWEEN 0 AND 100)",
                           name="ck_quality_range"),
        sa.Index("ix_mcq_job_status", "job_id", "status"),
        sa.Index("ix_mcq_topic_status", "topic_id", "status"),
    )


class MCQOption(Base):
    __tablename__ = "mcq_options"
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(sa.ForeignKey("mcq_candidates.id"), index=True)
    letter: Mapped[str] = mapped_column(sa.String(1))
    text: Mapped[str] = mapped_column(sa.Text)
    is_correct_generated: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    candidate: Mapped[MCQCandidate] = relationship(back_populates="options")
    __table_args__ = (sa.UniqueConstraint("candidate_id", "letter"),)


class MCQValidationResult(Base):
    __tablename__ = "mcq_validation_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(sa.ForeignKey("mcq_candidates.id"), index=True)
    stage: Mapped[str] = mapped_column(sa.String(32))   # structure|answer|distractor|notation|dedup|quality|sympy
    verdict: Mapped[str] = mapped_column(sa.String(16))  # PASS|FAIL|UNCERTAIN
    score: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    details_json: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[dt.datetime] = mapped_column(sa.DateTime, default=utcnow)
    candidate: Mapped[MCQCandidate] = relationship(back_populates="validation_results")


class MCQSource(Base):
    """Evidence bridge: candidate -> source (+chunk, +verbatim evidence span)."""
    __tablename__ = "mcq_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(sa.ForeignKey("mcq_candidates.id"), index=True)
    source_id: Mapped[int] = mapped_column(sa.ForeignKey("academic_sources.id"))
    chunk_id: Mapped[int | None] = mapped_column(sa.ForeignKey("document_chunks.id"))
    evidence_text: Mapped[str | None] = mapped_column(sa.Text)
    candidate: Mapped[MCQCandidate] = relationship(back_populates="sources")


class MCQReview(Base):
    __tablename__ = "mcq_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(sa.ForeignKey("mcq_candidates.id"), index=True)
    reviewer_id: Mapped[int] = mapped_column(sa.ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(sa.String(16))   # approve|reject|edit|flag|regen
    reason: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[dt.datetime] = mapped_column(sa.DateTime, default=utcnow)
    candidate: Mapped[MCQCandidate] = relationship(back_populates="reviews")


# ------------------------------------------------------ RAG tables (Phase 3)
class AcademicSource(Base):
    __tablename__ = "academic_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    origin: Mapped[str] = mapped_column(sa.String(8))          # local | web
    url: Mapped[str | None] = mapped_column(sa.String(2048))
    title: Mapped[str] = mapped_column(sa.String(512))
    author: Mapped[str | None] = mapped_column(sa.String(255))
    publisher: Mapped[str | None] = mapped_column(sa.String(255))
    domain: Mapped[str | None] = mapped_column(sa.String(255))
    license_note: Mapped[str | None] = mapped_column(sa.Text)
    priority: Mapped[int] = mapped_column(sa.Integer, default=50)
    quality_tier: Mapped[str] = mapped_column(sa.String(16), default="unrated")
    retrieved_at: Mapped[dt.datetime] = mapped_column(sa.DateTime, default=utcnow)


class SourceDocument(Base):
    __tablename__ = "source_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(sa.ForeignKey("academic_sources.id"), index=True)
    file_hash: Mapped[str] = mapped_column(sa.String(64), index=True)
    storage_path: Mapped[str | None] = mapped_column(sa.String(1024))
    extracted_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(sa.ForeignKey("source_documents.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(sa.Integer)
    text: Mapped[str] = mapped_column(sa.Text)
    page: Mapped[int | None] = mapped_column(sa.Integer)
    section: Mapped[str | None] = mapped_column(sa.Text)
    token_count: Mapped[int | None] = mapped_column(sa.Integer)
    qdrant_point_id: Mapped[str | None] = mapped_column(sa.String(64))
    meta_json: Mapped[str | None] = mapped_column(sa.Text)
    __table_args__ = (sa.UniqueConstraint("document_id", "chunk_index"),)
