"""Generation-job endpoints: create, inspect, start/pause/resume/cancel (FR-7).

MVP execution model: POST /start runs the pipeline synchronously in-process for
small targets and returns immediately for larger ones only after queueing —
Phase 2b swaps the sync call for an RQ enqueue behind the same API contract.
Status transitions are validated here; workers never set terminal states blindly.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.core.security import CurrentUser, audit, require_roles
from backend.database.session import get_db
from backend.models.entities import GenerationJob, GenerationJobTopic, JobStatus, MCQCandidate, Subject, Topic
from backend.schemas.api_dto import JobCreateIn, JobOut, JobProgress
from backend.workers.pipeline import process_job

router = APIRouter(prefix="/generation-jobs", tags=["jobs"])

DEFAULT_BLOOM = {"remember": 0.25, "understand": 0.30, "apply": 0.30, "analyze": 0.15}
DEFAULT_DIFF = {"easy": 0.30, "medium": 0.50, "hard": 0.20}

CanManage = Depends(require_roles("Administrator", "QuestionGenerator"))


def _job_out(db: Session, job: GenerationJob) -> JobOut:
    counts = _status_counts(db, job.id)
    generated_total = sum(counts.values())
    target = round(job.target_approved * job.overgeneration_factor)
    progress = JobProgress(
        requested=job.target_approved,
        candidate_target=target,
        generated=generated_total,
        invalid=counts.get("INVALID", 0),
        pending_review=counts.get("PENDING_REVIEW", 0),
        approved=counts.get("APPROVED", 0),
        progress_pct=round(min(100.0, 100.0 * generated_total / max(1, target)), 1),
    )
    return JobOut(id=job.id, name=job.name, subject_id=job.subject_id, status=job.status,
                  target_approved=job.target_approved, num_options=job.num_options,
                  overgeneration_factor=job.overgeneration_factor,
                  retrieval_enabled=job.retrieval_enabled, model_id=job.model_id,
                  created_at=job.created_at, updated_at=job.updated_at, progress=progress)


def _status_counts(db: Session, job_id: int) -> dict[str, int]:
    from sqlalchemy import func
    rows = (db.query(MCQCandidate.status, func.count(MCQCandidate.id))
            .filter(MCQCandidate.job_id == job_id).group_by(MCQCandidate.status).all())
    return {s: c for s, c in rows}


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def create_job(body: JobCreateIn, user: CurrentUser, db: Session = Depends(get_db)):
    s = get_settings()
    if body.target_approved > s.max_questions_per_job:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"target exceeds max_questions_per_job={s.max_questions_per_job}")
    if body.idempotency_key:
        existing = db.query(GenerationJob).filter_by(
            idempotency_key=body.idempotency_key).one_or_none()
        if existing:
            return _job_out(db, existing)          # replay-safe (design doc 06 §API)

    if db.get(Subject, body.subject_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subject not found")
    topic_ids = [t.topic_id for t in body.topics]
    found = db.query(Topic).filter(Topic.id.in_(topic_ids),
                                   Topic.subject_id == body.subject_id).count()
    if found != len(set(topic_ids)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "one or more topics missing or not belonging to subject")

    job = GenerationJob(
        name=body.name, subject_id=body.subject_id, status=JobStatus.QUEUED,
        target_approved=body.target_approved, num_options=body.num_options,
        overgeneration_factor=body.overgeneration_factor,
        bloom_dist_json=json.dumps(body.bloom_distribution or DEFAULT_BLOOM),
        difficulty_dist_json=json.dumps(body.difficulty_distribution or DEFAULT_DIFF),
        retrieval_enabled=body.retrieval_enabled and s.internet_retrieval_enabled,
        model_id=body.model_id, idempotency_key=body.idempotency_key,
        created_by=user.id,
    )
    db.add(job)
    db.flush()
    for t in body.topics:
        db.add(GenerationJobTopic(job_id=job.id, topic_id=t.topic_id, weight=t.weight))
    db.commit()
    audit(db, user, "job.create", "generation_jobs", job.id,
          {"target": body.target_approved, "topics": topic_ids})
    return _job_out(db, job)


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return _job_out(db, job)


@router.post("/{job_id}/start", response_model=JobOut)
def start_job(job_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    CanManage  # documented dependency marker (real enforcement below)
    if user.role not in ("Administrator", "QuestionGenerator"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cannot manage jobs")
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot start job in {job.status}")
    if get_settings().queue_enabled:
        # Phase 2b: enqueue on RQ instead of running inline
        from backend.workers.queue import enqueue_job
        enqueue_job(job.id)
    else:
        process_job(db, job.id)
    db.refresh(job)
    audit(db, user, "job.start", "generation_jobs", job.id)
    return _job_out(db, job)


def _set_status(db: Session, job: GenerationJob, new: str, allowed_from: tuple[str, ...]):
    if job.status not in allowed_from:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"cannot move {job.status} -> {new}")
    job.status = new
    db.commit()


@router.post("/{job_id}/pause", response_model=JobOut)
def pause_job(job_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    if user.role not in ("Administrator", "QuestionGenerator"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cannot manage jobs")
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    _set_status(db, job, JobStatus.PAUSED, (JobStatus.RUNNING,))
    audit(db, user, "job.pause", "generation_jobs", job.id)
    return _job_out(db, job)


@router.post("/{job_id}/resume", response_model=JobOut)
def resume_job(job_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    if user.role not in ("Administrator", "QuestionGenerator"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cannot manage jobs")
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    _set_status(db, job, JobStatus.RUNNING, (JobStatus.PAUSED,))
    if not get_settings().queue_enabled:
        process_job(db, job.id)
        db.refresh(job)
    audit(db, user, "job.resume", "generation_jobs", job.id)
    return _job_out(db, job)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    if user.role not in ("Administrator", "QuestionGenerator"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cannot manage jobs")
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    _set_status(db, job, JobStatus.CANCELLED,
                (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED))
    audit(db, user, "job.cancel", "generation_jobs", job.id)
    return _job_out(db, job)
