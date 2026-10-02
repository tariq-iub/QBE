"""Document & Knowledge-Pack endpoints (Phase 3).

POST /documents            multipart upload -> ingest (local origin)
GET  /documents            list ingested documents with chunk counts
GET  /documents/{id}       document detail incl. source metadata
DELETE /documents/{id}     remove doc + chunks + vector points (Administrator)
GET  /topics/{id}/knowledge-pack   assembled Topic Knowledge Pack preview
GET  /topics/{id}/evidence         raw retrieval hits (reviewer evidence panel)

Uploads are treated as UNTRUSTED data: ingestion sanitizes text and the pack
wraps every chunk in fenced DATA blocks (§35); nothing here executes content.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from backend.core.security import CurrentUser, audit, require_roles
from backend.database.session import get_db
from backend.models.entities import (
    AcademicSource, DocumentChunk, SourceDocument, Topic,
)
from backend.rag import ingest as ingest_svc
from backend.rag.knowledge_pack import build_pack, retrieve_for_topic

router = APIRouter(tags=["rag"])


def _doc_summary(db: Session, doc: SourceDocument) -> dict:
    src = db.get(AcademicSource, doc.source_id)
    return {
        "document_id": doc.id,
        "source_id": doc.source_id,
        "title": src.title if src else None,
        "origin": src.origin if src else None,
        "url": src.url if src else None,
        "license_note": src.license_note if src else None,
        "file_hash": doc.file_hash,
        "storage_path": doc.storage_path,
        "extracted_at": doc.extracted_at.isoformat() if doc.extracted_at else None,
        "chunk_count": db.query(DocumentChunk).filter_by(document_id=doc.id).count(),
    }


WriteRoles = require_roles("Administrator", "QuestionGenerator", "SubjectExpert")


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    user: Annotated[CurrentUser, Depends(WriteRoles)],
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    topic_ids: str | None = Form(default=None),      # comma-separated ints
    license_note: str | None = Form(default=None),
):
    """Ingest one instructor-provided document (txt/md/pdf-bytes). Idempotent
    by SHA-256: re-uploading an identical file returns the existing rows."""
    data = await file.read()
    tids = [int(x) for x in (topic_ids or "").split(",") if x.strip().isdigit()]
    for tid in tids:
        if db.get(Topic, tid) is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"unknown topic id {tid}")
    try:
        result = ingest_svc.ingest_bytes(
            db, data=data, filename=file.filename or "upload.txt",
            title=title, origin="local", license_note=license_note, topic_ids=tids,
        )
    except ingest_svc.IngestError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    audit(db, user, "document.ingest", "source_documents", result["document_id"],
          {"filename": file.filename, "chunks": result["chunks"],
           "deduplicated": result["deduplicated"]})
    doc = db.get(SourceDocument, result["document_id"])
    return {**_doc_summary(db, doc), "deduplicated": result["deduplicated"]}


@router.get("/documents")
def list_documents(user: CurrentUser, db: Session = Depends(get_db),
                   limit: int = 50, offset: int = 0):
    q = db.query(SourceDocument).order_by(SourceDocument.id.desc())
    total = q.count()
    docs = q.offset(offset).limit(min(limit, 200)).all()
    return {"total": total, "items": [_doc_summary(db, d) for d in docs]}


@router.get("/documents/{document_id}")
def get_document(document_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    doc = db.get(SourceDocument, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    out = _doc_summary(db, doc)
    out["chunks_preview"] = [
        {"chunk_id": c.id, "chunk_index": c.chunk_index, "page": c.page,
         "section": c.section, "excerpt": c.text[:240]}
        for c in db.query(DocumentChunk).filter_by(document_id=doc.id)
                    .order_by(DocumentChunk.chunk_index).limit(10).all()
    ]
    return out


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    user: Annotated[CurrentUser, Depends(require_roles("Administrator"))],
    db: Session = Depends(get_db),
):
    if not ingest_svc.delete_document(db, document_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    audit(db, user, "document.delete", "source_documents", document_id)


@router.get("/topics/{topic_id}/knowledge-pack")
def knowledge_pack(topic_id: int, user: CurrentUser, db: Session = Depends(get_db),
                   query: str | None = None):
    """Reviewer/admin preview of the exact material generation will see."""
    if db.get(Topic, topic_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "topic not found")
    pack = build_pack(db, topic_id, extra_query=query)
    return {
        "topic_id": pack.topic_id,
        "topic_name": pack.topic_name,
        "subject_hint": pack.subject_hint,
        "grounded": pack.has_grounding(),
        "concepts": pack.concepts,
        "formulas": pack.formulas,
        "misconceptions": pack.misconceptions,
        "citations": pack.citations(),
        "evidence": [
            {"chunk_id": e.chunk_id, "source_id": e.source_id, "page": e.page,
             "section": e.section, "score": round(e.score, 4),
             "origin": e.origin, "url": e.url, "title": e.title,
             "excerpt": e.text[:400]}
            for e in pack.evidence
        ],
        "embedding_model": pack.embedding_model,
        "params": pack.built_at_params,
    }


@router.get("/topics/{topic_id}/evidence")
def topic_evidence(topic_id: int, user: CurrentUser, db: Session = Depends(get_db),
                   top_k: int | None = None):
    if db.get(Topic, topic_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "topic not found")
    hits = retrieve_for_topic(db, topic_id, top_k=top_k)
    return {"topic_id": topic_id,
            "items": [{"chunk_id": h.chunk_id, "source_id": h.source_id,
                       "document_id": h.document_id, "page": h.page,
                       "section": h.section, "score": round(h.score, 4)}
                      for h in hits]}
