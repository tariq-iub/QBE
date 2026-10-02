"""Question endpoints: list/filter, detail with full provenance, review actions.

Review actions enforce the MCQStatus transition map (backend/models/enums.py).
Approval is human-only (ADR-008): no automated path sets APPROVED.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, selectinload

from backend.core.config import get_settings
from backend.core.security import CurrentUser, audit
from backend.database.session import get_db
from backend.models.entities import MCQCandidate, MCQReview
from backend.models.enums import MCQStatus, can_transition
from backend.schemas.api_dto import QuestionOut, QuestionPage, PageMeta, ReviewActionIn

router = APIRouter(tags=["questions"])

REVIEW_ROLES = ("Administrator", "AcademicReviewer", "SubjectExpert")


def _to_out(c: MCQCandidate) -> QuestionOut:
    prov = {
        "job_id": c.job_id,
        "model_id": c.model_id,
        "prompt_template_id": c.prompt_template_id,
        "generation_params": json.loads(c.generation_params_json or "{}"),
        "seed": c.seed,
        "created_at": c.created_at.isoformat(),
    }
    return QuestionOut(
        id=c.id, job_id=c.job_id, topic_id=c.topic_id, stem=c.stem,
        explanation=c.explanation, bloom_level=c.bloom_level, difficulty=c.difficulty,
        question_type=c.question_type, status=c.status, quality_score=c.quality_score,
        contains_latex=c.contains_latex,
        options=[{"letter": o.letter, "text": o.text,
                  "is_correct_generated": o.is_correct_generated} for o in c.options],
        provenance=prov,
        validations=[{"stage": v.stage, "verdict": v.verdict,
                      "details_json": v.details_json, "created_at": v.created_at}
                     for v in c.validation_results],
        sources=[{"source_id": s.source_id, "chunk_id": s.chunk_id,
                  "evidence_text": s.evidence_text} for s in c.sources],
        created_at=c.created_at,
    )


@router.get("/questions", response_model=QuestionPage)
def list_questions(
    user: CurrentUser,
    db: Session = Depends(get_db),
    job_id: int | None = None,
    topic_id: int | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    difficulty: str | None = None,
    bloom: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int | None = None,
):
    if status_filter is not None:
        try:
            st = MCQStatus(status_filter)
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"unknown status {status_filter!r}")
    else:
        st = None
    q = db.query(MCQCandidate).options(
        selectinload(MCQCandidate.options),
        selectinload(MCQCandidate.validation_results),
        selectinload(MCQCandidate.sources))
    if job_id:
        q = q.filter(MCQCandidate.job_id == job_id)
    if topic_id:
        q = q.filter(MCQCandidate.topic_id == topic_id)
    if st:
        q = q.filter(MCQCandidate.status == st.value)
    if difficulty:
        q = q.filter(MCQCandidate.difficulty == difficulty)
    if bloom:
        q = q.filter(MCQCandidate.bloom_level == bloom)
    s = get_settings()
    size = min(page_size or s.page_size_default, s.page_size_max)
    total = q.count()
    items = q.order_by(MCQCandidate.id).offset((page - 1) * size).limit(size).all()
    return QuestionPage(items=[_to_out(c) for c in items],
                        meta=PageMeta(page=page, page_size=size, total=total))


@router.get("/questions/{question_id}", response_model=QuestionOut)
def get_question(question_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    c = db.get(MCQCandidate, question_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "question not found")
    return _to_out(c)


_ACTION_TO_STATUS = {
    "approve": MCQStatus.APPROVED,
    "reject": MCQStatus.REJECTED,
    "needs_revision": MCQStatus.NEEDS_REVISION,
    "flag": None,   # keeps status, records review row
}


@router.post("/questions/{question_id}/review", response_model=QuestionOut)
def review_question(question_id: int, body: ReviewActionIn, user: CurrentUser,
                    db: Session = Depends(get_db)):
    if user.role not in REVIEW_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"role {user.role} cannot review questions")
    c = db.get(MCQCandidate, question_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "question not found")

    new_status = _ACTION_TO_STATUS[body.action]
    if new_status is not None:
        if not can_transition(c.status, new_status):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"illegal transition {c.status} -> {new_status}")
        c.status = new_status.value
    db.add(MCQReview(candidate_id=c.id, reviewer_id=user.id, action=body.action,
                     reason=body.reason))
    db.commit()
    audit(db, user, f"question.{body.action}", "mcq_candidates", c.id,
          {"reason": body.reason})
    db.refresh(c)
    return _to_out(c)


# convenience aliases matching the master-prompt examples (§28)
@router.post("/questions/{question_id}/approve", response_model=QuestionOut)
def approve(qid: int, user: CurrentUser, db: Session = Depends(get_db)):
    return review_question(qid, ReviewActionIn(action="approve"), user, db)


@router.post("/questions/{question_id}/reject", response_model=QuestionOut)
def reject(qid: int, user: CurrentUser, db: Session = Depends(get_db)):
    return review_question(qid, ReviewActionIn(action="reject"), user, db)
