"""API request/response models (Pydantic v2)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

BloomDist = dict[Literal["remember", "understand", "apply", "analyze"], float]
DiffDist = dict[Literal["easy", "medium", "hard"], float]


class TokenRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class SubjectOut(BaseModel):
    id: int
    name: str
    code: str | None
    program: str | None
    semester: str | None
    model_config = {"from_attributes": True}


class TopicOut(BaseModel):
    id: int
    subject_id: int
    name: str
    teaching_hours: float | None
    weight: float | None
    importance: int
    model_config = {"from_attributes": True}


class JobTopicIn(BaseModel):
    topic_id: int
    weight: float | None = None


class JobCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    subject_id: int
    topics: list[JobTopicIn] = Field(min_length=1, max_length=100)
    target_approved: int = Field(gt=0)
    num_options: Literal[4, 5] = 4
    overgeneration_factor: float = Field(default=1.35, gt=1.0, le=2.0)
    bloom_distribution: BloomDist | None = None
    difficulty_distribution: DiffDist | None = None
    retrieval_enabled: bool = False
    model_id: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("bloom_distribution")
    @classmethod
    def _bloom_sum(cls, v):
        if v is not None and abs(sum(v.values()) - 1.0) > 0.01:
            raise ValueError("bloom distribution must sum to 1.0")
        return v

    @field_validator("difficulty_distribution")
    @classmethod
    def _diff_sum(cls, v):
        if v is not None and abs(sum(v.values()) - 1.0) > 0.01:
            raise ValueError("difficulty distribution must sum to 1.0")
        return v


class JobProgress(BaseModel):
    requested: int
    candidate_target: int
    generated: int
    invalid: int
    pending_review: int
    approved: int
    progress_pct: float


class JobOut(BaseModel):
    id: int
    name: str
    subject_id: int
    status: str
    target_approved: int
    num_options: int
    overgeneration_factor: float
    retrieval_enabled: bool
    model_id: int | None
    created_at: datetime
    updated_at: datetime
    progress: JobProgress


class OptionOut(BaseModel):
    letter: str
    text: str
    is_correct_generated: bool
    model_config = {"from_attributes": True}


class ValidationResultOut(BaseModel):
    stage: str
    verdict: str
    details_json: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class SourceRefOut(BaseModel):
    source_id: int
    chunk_id: int | None
    evidence_text: str | None
    model_config = {"from_attributes": True}


class QuestionOut(BaseModel):
    id: int
    job_id: int
    topic_id: int
    stem: str
    explanation: str | None
    bloom_level: str
    difficulty: str
    question_type: str | None
    status: str
    quality_score: int | None
    contains_latex: bool
    options: list[OptionOut]
    provenance: dict
    validations: list[ValidationResultOut]
    sources: list[SourceRefOut]
    created_at: datetime
    model_config = {"from_attributes": True}


class ReviewActionIn(BaseModel):
    action: Literal["approve", "reject", "needs_revision", "flag"]
    reason: str | None = Field(default=None, max_length=2000)


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int


class QuestionPage(BaseModel):
    items: list[QuestionOut]
    meta: PageMeta
