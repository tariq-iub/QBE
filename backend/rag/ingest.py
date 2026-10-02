"""Ingestion service (Phase 3): upload -> dedupe by hash -> extract -> chunk ->
embed -> persist DocumentChunk rows + vector index points with full metadata.

Provenance rules (§6/§7): every chunk keeps source_id, document_id, page,
section and topic linkage; re-ingesting an identical file is idempotent.
"""
from __future__ import annotations

import json
import os
import uuid

from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.embeddings.providers import embed_texts
from backend.models.entities import AcademicSource, DocumentChunk, SourceDocument
from backend.rag import textproc
from backend.rag.vectorstore import VectorPoint, get_vector_store


class IngestError(ValueError):
    pass


def ingest_bytes(db: Session, *, data: bytes, filename: str, title: str | None = None,
                 origin: str = "local", url: str | None = None,
                 license_note: str | None = None, topic_ids: list[int] | None = None,
                 storage_dir: str | None = None) -> dict:
    """Idempotent ingestion of one document. Returns summary dict."""
    if not data:
        raise IngestError("empty file")
    s = get_settings()
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise IngestError(f"file exceeds {s.max_upload_mb} MB limit")

    file_hash = textproc.sha256_hex(data)
    existing_doc = db.query(SourceDocument).filter_by(file_hash=file_hash).first()
    if existing_doc:
        return {"document_id": existing_doc.id, "source_id": existing_doc.source_id,
                "chunks": db.query(DocumentChunk).filter_by(document_id=existing_doc.id).count(),
                "deduplicated": True}

    source = AcademicSource(origin=origin, url=url,
                            title=(title or filename)[:512], license_note=license_note)
    db.add(source)
    db.flush()

    doc = SourceDocument(source_id=source.id, file_hash=file_hash, extracted_at=None)
    if storage_dir:
        os.makedirs(storage_dir, exist_ok=True)
        safe = "".join(c for c in filename if c.isalnum() or c in "._-")[:128] or "upload.bin"
        path = os.path.join(storage_dir, f"{file_hash[:16]}_{safe}")
        with open(path, "wb") as f:
            f.write(data)
        doc.storage_path = path
    db.add(doc)
    db.flush()

    try:
        pages = textproc.extract_bytes(filename, data)
    except (ValueError, RuntimeError) as e:
        # unsupported type or corrupt/unparseable file (e.g. image-only PDF with
        # no object structure) -> surface as IngestError, roll back staged rows
        db.rollback()
        raise IngestError(str(e)) from e
    chunks = textproc.chunk_pages(pages, target_tokens=s.chunk_target_tokens,
                                  overlap_tokens=s.chunk_overlap_tokens)
    if not chunks:
        db.rollback()
        raise IngestError("no extractable text (scanned/image-only PDF? try OCR offline)")

    texts = [c["text"] for c in chunks]
    vectors = embed_texts(texts)

    topic_ids = topic_ids or []
    points: list[VectorPoint] = []
    for c, vec in zip(chunks, vectors):
        row = DocumentChunk(
            document_id=doc.id, chunk_index=c["chunk_index"], text=c["text"],
            page=c["page"], section=c["section"], token_count=c["token_count"],
            qdrant_point_id=str(uuid.uuid4()),
            meta_json=json.dumps({"subject_hint": None, "topic_ids": topic_ids,
                                  "source_id": source.id, "filename": filename}),
        )
        db.add(row)
        db.flush()
        payload = {
            "chunk_db_id": row.id, "document_id": doc.id, "source_id": source.id,
            "topic_ids": topic_ids, "page": c["page"], "section": c["section"],
            "text": c["text"][:2000],
        }
        points.append(VectorPoint(point_id=row.qdrant_point_id, vector=vec, payload=payload))

    doc.extracted_at = _utcnow()
    db.commit()
    get_vector_store().upsert(points)
    return {"document_id": doc.id, "source_id": source.id, "chunks": len(chunks),
            "deduplicated": False}


def delete_document(db: Session, document_id: int) -> bool:
    doc = db.get(SourceDocument, document_id)
    if not doc:
        return False
    get_vector_store().delete_by_payload({"document_id": document_id})
    db.query(DocumentChunk).filter_by(document_id=document_id).delete()
    src_id = doc.source_id
    db.delete(doc)
    # remove orphan source
    if db.query(SourceDocument).filter_by(source_id=src_id).count() <= 1:
        src = db.get(AcademicSource, src_id)
        if src:
            db.delete(src)
    db.commit()
    return True


def _utcnow():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
