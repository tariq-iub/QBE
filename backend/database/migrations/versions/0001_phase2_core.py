"""Phase 2 core schema: auth, academic cache, jobs, candidates, validation, RAG tables.

Targets PostgreSQL in production; written to be portable enough that `alembic
upgrade head` also succeeds on SQLite for dev. Provenance columns on
mcq_candidates are NOT NULL by design (NFR-6 / §47 reproducibility).

Revision ID: 0001
Revises:
Create Date: 2026-10-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(128), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="ReadOnly"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    op.create_table(
        "subjects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(64), nullable=True),
        sa.Column("program", sa.String(128), nullable=True),
        sa.Column("semester", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(64), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_subjects_external_id", "subjects", ["external_id"])

    op.create_table(
        "topics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("subjects.id"), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("teaching_hours", sa.Float(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("importance", sa.Integer(), nullable=False, server_default="3"),
    )
    op.create_index("ix_topics_subject_id", "topics", ["subject_id"])

    op.create_table(
        "subtopics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(), sa.ForeignKey("topics.id"), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
    )
    op.create_index("ix_subtopics_topic_id", "subtopics", ["topic_id"])

    op.create_table(
        "generation_models",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("quantization", sa.String(32), nullable=True),
        sa.Column("version_hash", sa.String(128), nullable=True),
        sa.Column("provider_kind", sa.String(32), nullable=False),
        sa.Column("params_default_json", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "prompt_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("template_text", sa.Text(), nullable=False),
        sa.Column("temperature", sa.Numeric(3, 2), nullable=False, server_default="0.30"),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="1500"),
        sa.UniqueConstraint("kind", "version", name="uq_prompt_kind_version"),
    )

    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("subjects.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("target_approved", sa.Integer(), nullable=False),
        sa.Column("num_options", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("overgeneration_factor", sa.Float(), nullable=False, server_default="1.35"),
        sa.Column("bloom_dist_json", sa.Text(), nullable=False),
        sa.Column("difficulty_dist_json", sa.Text(), nullable=False),
        sa.Column("retrieval_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_id", sa.Integer(), sa.ForeignKey("generation_models.id"), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("target_approved > 0", name="ck_target_positive"),
    )
    op.create_index("ix_generation_jobs_subject_id", "generation_jobs", ["subject_id"])
    op.create_index("ix_generation_jobs_status", "generation_jobs", ["status"])
    op.create_index("ix_generation_jobs_idempotency_key", "generation_jobs",
                    ["idempotency_key"], unique=True)

    op.create_table(
        "generation_job_topics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("generation_jobs.id"), nullable=False),
        sa.Column("topic_id", sa.Integer(), sa.ForeignKey("topics.id"), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("bloom_override_json", sa.Text(), nullable=True),
        sa.UniqueConstraint("job_id", "topic_id", name="uq_job_topic"),
    )
    op.create_index("ix_gjt_job_id", "generation_job_topics", ["job_id"])

    op.create_table(
        "job_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("generation_jobs.id"), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("batch_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state_json", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("job_id", "stage", name="uq_job_stage"),
    )
    op.create_index("ix_job_checkpoints_job_id", "job_checkpoints", ["job_id"])

    op.create_table(
        "academic_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("origin", sa.String(8), nullable=False),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("publisher", sa.String(255), nullable=True),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("license_note", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("quality_tier", sa.String(16), nullable=False, server_default="unrated"),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "source_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("academic_sources.id"), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=True),
        sa.Column("extracted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_source_documents_source_id", "source_documents", ["source_id"])
    op.create_index("ix_source_documents_file_hash", "source_documents", ["file_hash"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("source_documents.id"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("qdrant_point_id", sa.String(64), nullable=True),
        sa.Column("meta_json", sa.Text(), nullable=True),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_doc_chunk"),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])

    op.create_table(
        "mcq_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("generation_jobs.id"), nullable=False),
        sa.Column("topic_id", sa.Integer(), sa.ForeignKey("topics.id"), nullable=False),
        sa.Column("subtopic_id", sa.Integer(), sa.ForeignKey("subtopics.id"), nullable=True),
        sa.Column("stem", sa.Text(), nullable=False),
        sa.Column("stem_norm", sa.String(1024), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("bloom_level", sa.String(16), nullable=False),
        sa.Column("difficulty", sa.String(8), nullable=False),
        sa.Column("question_type", sa.String(32), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="GENERATED"),
        sa.Column("quality_score", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column("contains_latex", sa.Boolean(), nullable=False, server_default=sa.false()),
        # provenance — NOT NULL (NFR-6)
        sa.Column("model_id", sa.Integer(), sa.ForeignKey("generation_models.id"), nullable=False),
        sa.Column("prompt_template_id", sa.Integer(), sa.ForeignKey("prompt_templates.id"),
                  nullable=False),
        sa.Column("generation_params_json", sa.Text(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("quality_score IS NULL OR (quality_score BETWEEN 0 AND 100)",
                           name="ck_quality_range"),
    )
    op.create_index("ix_mcq_candidates_job_id", "mcq_candidates", ["job_id"])
    op.create_index("ix_mcq_candidates_topic_id", "mcq_candidates", ["topic_id"])
    op.create_index("ix_mcq_candidates_stem_norm", "mcq_candidates", ["stem_norm"])
    op.create_index("ix_mcq_job_status", "mcq_candidates", ["job_id", "status"])
    op.create_index("ix_mcq_topic_status", "mcq_candidates", ["topic_id", "status"])

    op.create_table(
        "mcq_options",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("mcq_candidates.id"), nullable=False),
        sa.Column("letter", sa.String(1), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_correct_generated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("candidate_id", "letter", name="uq_candidate_letter"),
    )
    op.create_index("ix_mcq_options_candidate_id", "mcq_options", ["candidate_id"])

    op.create_table(
        "mcq_validation_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("mcq_candidates.id"), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_mvr_candidate_id", "mcq_validation_results", ["candidate_id"])

    op.create_table(
        "mcq_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("mcq_candidates.id"), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("academic_sources.id"), nullable=False),
        sa.Column("chunk_id", sa.Integer(), sa.ForeignKey("document_chunks.id"), nullable=True),
        sa.Column("evidence_text", sa.Text(), nullable=True),
    )
    op.create_index("ix_mcq_sources_candidate_id", "mcq_sources", ["candidate_id"])

    op.create_table(
        "mcq_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("mcq_candidates.id"), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_mcq_reviews_candidate_id", "mcq_reviews", ["candidate_id"])


def downgrade() -> None:
    # indexes are dropped together with their tables on both SQLite and Postgres
    for tbl in ("mcq_reviews", "mcq_sources", "mcq_validation_results", "mcq_options",
                "mcq_candidates", "document_chunks", "source_documents", "academic_sources",
                "job_checkpoints", "generation_job_topics", "generation_jobs",
                "prompt_templates", "generation_models", "subtopics", "topics",
                "subjects", "audit_logs", "users"):
        op.drop_table(tbl)
