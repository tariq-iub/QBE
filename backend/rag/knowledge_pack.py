"""Topic Knowledge Pack builder + grounded retrieval (Phase 3, design doc 05 §8).

The Knowledge Pack is the ONLY material MCQ generation sees (ADR-006): raw
chunks are wrapped as fenced, labelled DATA blocks so prompt-injection from
ingested documents cannot masquerade as instructions (§35).

MVP extraction is deterministic: retrieved chunks + citation metadata assembled
into a pack. The optional LLM `knowledge_extraction_v1` distillation pass runs
only when explicitly enabled (`use_llm=True`) — never implicitly, to keep cost
and failure modes bounded on the target workstation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.embeddings.providers import embed_texts
from backend.models.entities import AcademicSource, DocumentChunk, Topic
from backend.rag.vectorstore import get_vector_store


@dataclass
class EvidenceChunk:
    chunk_id: int
    source_id: int
    document_id: int
    text: str
    page: int | None
    section: str | None
    score: float
    origin: str = "local"
    url: str | None = None
    title: str | None = None


@dataclass
class TopicKnowledgePack:
    topic_id: int
    topic_name: str
    subject_hint: str | None
    evidence: list[EvidenceChunk] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    formulas: list[str] = field(default_factory=list)
    misconceptions: list[str] = field(default_factory=list)
    embedding_model: str = ""
    built_at_params: dict = field(default_factory=dict)

    def has_grounding(self) -> bool:
        return len(self.evidence) > 0

    def context_blocks(self) -> str:
        """Prompt-ready representation: each chunk fenced and labelled as DATA."""
        out = []
        for i, e in enumerate(self.evidence, 1):
            cite = f"[source {e.source_id} chunk {e.chunk_id}"
            if e.page:
                cite += f" p.{e.page}"
            if e.section:
                cite += f" section={e.section!r}"
            cite += "]"
            body = e.text.replace("```", "'''")  # never allow fence breakout
            out.append(f"----- BEGIN REFERENCE DATA {i} {cite} -----\n{body}\n"
                       f"------ END REFERENCE DATA {i} ------")
        return "\n\n".join(out)

    def citations(self) -> list[dict]:
        seen: dict[int, dict] = {}
        for e in self.evidence:
            seen.setdefault(e.source_id, {
                "source_id": e.source_id, "origin": e.origin, "url": e.url,
                "title": e.title})
        return list(seen.values())

    def chunk_map(self) -> dict[int, int]:
        """prompt_ref (1..k, matching context_blocks numbering) -> chunk db id."""
        return {i: e.chunk_id for i, e in enumerate(self.evidence, 1)}

    def to_json(self) -> str:
        return json.dumps({
            "topic_id": self.topic_id, "topic_name": self.topic_name,
            "subject_hint": self.subject_hint,
            "concepts": self.concepts, "formulas": self.formulas,
            "misconceptions": self.misconceptions,
            "evidence": [e.chunk_id for e in self.evidence],
            "citations": self.citations(),
            "embedding_model": self.embedding_model,
            "params": self.built_at_params,
        })


def retrieve_for_topic(db: Session, topic_id: int, *, query: str | None = None,
                       top_k: int | None = None,
                       min_score: float | None = None) -> list[EvidenceChunk]:
    """Vector search filtered to chunks linked to this topic (§7 metadata filter)."""
    s = get_settings()
    top_k = top_k or s.retrieval_top_k
    min_score = s.retrieval_min_score if min_score is None else min_score

    topic = db.get(Topic, topic_id)
    qtext = query or (topic.name if topic else str(topic_id))
    qvec = embed_texts([qtext])[0]

    hits = get_vector_store().search(qvec, limit=top_k * 3, flt={"topic_ids": [topic_id]})
    evidence: list[EvidenceChunk] = []
    for h in hits:
        if h.score < min_score:
            continue
        cid = h.payload.get("chunk_db_id")
        row = db.get(DocumentChunk, cid) if cid else None
        if row is None:                      # index/DB drift -> drop stale point
            continue
        src = db.get(AcademicSource, row.document.source_id)
        evidence.append(EvidenceChunk(
            chunk_id=row.id, source_id=src.id, document_id=row.document_id,
            text=row.text, page=row.page, section=row.section, score=h.score,
            origin=src.origin, url=src.url, title=src.title))
        if len(evidence) >= top_k:
            break
    return evidence


def build_pack(db: Session, topic_id: int, *, extra_query: str | None = None,
               use_llm: bool = False, provider=None, top_k: int | None = None
               ) -> TopicKnowledgePack:
    """Assemble the Knowledge Pack for a topic. Empty pack => no grounding =>
    generation must be skipped for that topic (§20)."""
    s = get_settings()
    topic = db.get(Topic, topic_id)
    evidence = retrieve_for_topic(db, topic_id,
                                  query=extra_query or (topic.name if topic else None),
                                  top_k=top_k)
    pack = TopicKnowledgePack(
        topic_id=topic_id,
        topic_name=topic.name if topic else f"topic-{topic_id}",
        subject_hint=topic.subject.code if topic and topic.subject else None,
        evidence=evidence,
        embedding_model=getattr(__import__("backend.embeddings.providers",
                                           fromlist=["get_embedder"]).get_embedder().info,
                                "model_id", ""),
        built_at_params={"top_k": top_k or s.retrieval_top_k,
                         "min_score": s.retrieval_min_score},
    )
    if use_llm and provider is not None and pack.has_grounding():
        _distill_with_llm(pack, provider)
    return pack


def _distill_with_llm(pack: TopicKnowledgePack, provider) -> None:  # pragma: no cover
    """Optional concept/formula/misconception distillation (knowledge_extraction_v1)."""
    from backend.llm.base import GenParams
    from backend.prompts.templates import PROMPT_REGISTRY
    sys_txt, user_tpl = PROMPT_REGISTRY["knowledge_extraction_v1"]
    user = user_tpl.format(subject=pack.subject_hint or "-", topic=pack.topic_name,
                           context_blocks=pack.context_blocks())
    try:
        result = provider.complete_json(sys_txt, user, GenParams(temperature=0.1),
                                        schema_name="mcq_batch_v1", attempts=1)
        pack.concepts = list(result.get("learning_concepts", []))[:20]
        pack.formulas = list(result.get("formulas", []))[:20]
        pack.misconceptions = list(result.get("common_misconceptions", []))[:10]
    except Exception:
        # Distillation is best-effort; deterministic pack remains valid.
        pass
