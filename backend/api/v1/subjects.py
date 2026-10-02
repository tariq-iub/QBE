"""Subjects/topics endpoints — served from the academic cache (ADR-009).

MVP: cache rows are populated by scripts/import_seed.py or a future sync job.
Phase 2b adds POST /subjects/sync reading the legacy adapter (read-only).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.security import CurrentUser
from backend.database.session import get_db
from backend.models.entities import Subject, Topic
from backend.schemas.api_dto import SubjectOut, TopicOut

router = APIRouter(tags=["academic"])


@router.get("/subjects", response_model=list[SubjectOut])
def list_subjects(user: CurrentUser, db: Session = Depends(get_db)):
    return db.query(Subject).order_by(Subject.name).all()


@router.get("/subjects/{subject_id}", response_model=SubjectOut)
def get_subject(subject_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    s = db.get(Subject, subject_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subject not found")
    return s


@router.get("/subjects/{subject_id}/topics", response_model=list[TopicOut])
def list_topics(subject_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    if db.get(Subject, subject_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subject not found")
    return db.query(Topic).filter_by(subject_id=subject_id).order_by(Topic.name).all()
