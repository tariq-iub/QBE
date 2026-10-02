"""Synchronous MVP pipeline runner (Phase 2a).

process_job(): plan -> generate batches -> structural validation -> position
shuffle -> persist candidates + validation results. Later phases insert stages:
Phase 3 knowledge packs, Phase 5b independent answer verification,
Phase 7 dedup/quality classification.

Resumability (FR-9): each completed batch advances a `generate` checkpoint;
a restarted run skips already-checkpointed batches instead of regenerating them.
"""
from __future__ import annotations

import json
import random
import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.llm.base import GenParams, ILLMProvider, LLMError
from backend.models.entities import (
    GenerationJob,
    GenerationModel,
    JobCheckpoint,
    JobStatus,
    MCQCandidate,
    MCQOption,
    MCQValidationResult,
    PromptTemplate,
    Topic,
)
from backend.models.enums import MCQStatus
from backend.validation.position import shuffle_options
from backend.validation.structure import validate_mcq
from backend.workers.planner import BatchPlan, build_plan


def normalize_stem(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = re.sub(r"\s+", " ", t)
    return re.sub(r"[^\w \(\)\$\.\,\?]", "", t).strip()[:1000]


@dataclass
class BatchOutcome:
    generated: int
    persisted: int
    invalid: int
    skipped: int = 0   # batches skipped for lack of grounding evidence (§20)


def _prompt_parts(template: PromptTemplate) -> tuple[str, str]:
    sys_txt, _, user_txt = template.template_text.partition("\n=====SPLIT=====\n")
    return sys_txt, user_txt


def _context_blocks_for_topic(db: Session, topic_id: int):
    """Phase 3: grounded retrieval via Topic Knowledge Packs (design doc 05 §8).

    Returns None when no evidence exists (§20: never generate factual questions
    without supporting evidence — the batch is skipped, not hallucinated).
    Otherwise returns (fenced DATA blocks, prompt_ref -> chunk_db_id map,
    citation list).
    """
    from backend.rag.knowledge_pack import build_pack

    pack = build_pack(db, topic_id)
    if not pack.has_grounding():
        return None
    return pack.context_blocks(), pack.chunk_map(), pack.citations()


def _persist_candidate(db: Session, job: GenerationJob, batch: BatchPlan, mcq: dict,
                       model: GenerationModel, prompt: PromptTemplate,
                       params: GenParams, *, status: str,
                       chunk_map: dict[int, int] | None = None,
                       citations: list[dict] | None = None) -> MCQCandidate:
    stem = mcq["question"]
    cand = MCQCandidate(
        job_id=job.id, topic_id=batch.topic_id, stem=stem,
        stem_norm=normalize_stem(stem), explanation=mcq.get("explanation"),
        bloom_level=mcq["bloom_level"], difficulty=mcq["difficulty"],
        question_type=mcq.get("question_type"), status=status,
        confidence=mcq.get("confidence"),
        contains_latex=("$" in stem or any("$" in o or "\\" in o for o in mcq["options"])),
        model_id=model.id, prompt_template_id=prompt.id,
        generation_params_json=json.dumps({
            "temperature": params.temperature, "top_p": params.top_p,
            "max_tokens": params.max_tokens, "provider": model.provider_kind,
        }),
        seed=params.seed,
    )
    for i, opt in enumerate(mcq["options"]):
        cand.options.append(MCQOption(letter=chr(ord("A") + i), text=opt,
                                      is_correct_generated=(i == int(mcq["correct_option"]))))
    # §20 grounding provenance: map cited evidence refs to real chunk/source rows.
    if chunk_map:
        seen_chunks: set[int] = set()
        for ref in mcq.get("evidence_refs", []):
            cid = chunk_map.get(int(ref))
            if cid is None or cid in seen_chunks:
                continue
            seen_chunks.add(cid)
            row = db.get(DocumentChunk, cid)
            if row is None:
                continue  # never fabricate a reference (§52)
            cand.sources.append(MCQSource(
                source_id=row.document.source_id, chunk_id=row.id,
                evidence_text=row.text[:4000]))
    db.add(cand)
    db.flush()
    return cand


def process_batch(db: Session, job: GenerationJob, batch: BatchPlan, *,
                  provider: ILLMProvider, model: GenerationModel,
                  prompt: PromptTemplate, rng: random.Random) -> BatchOutcome:
    topic = db.get(Topic, batch.topic_id)
    ctx = _context_blocks_for_topic(db, batch.topic_id)
    if ctx is None:
        # §20: no retrieved evidence => skip generation for this batch entirely.
        return BatchOutcome(generated=0, persisted=0, invalid=0, skipped=batch.count)
    context_blocks, chunk_map, citations = ctx
    sys_txt, user_tpl = _prompt_parts(prompt)
    bloom_mix = ", ".join(f"{k}:{v}" for k, v in batch.bloom_mix.items() if v)
    diff_mix = ", ".join(f"{k}:{v}" for k, v in batch.difficulty_mix.items() if v)
    user = user_tpl.format(
        subject=topic.subject.name if topic.subject else "?",
        topic=topic.name,
        subtopics=", ".join(s.name for s in topic.subtopics) or "-",
        concepts="- core principles of the topic",
        definitions="- key terms with precise definitions",
        formulas="- relevant equations in LaTeX",
        misconceptions="- typical student errors (distractor fuel only)",
        count=batch.count,
        bloom_mix=bloom_mix, difficulty_mix=diff_mix,
        question_types=", ".join(batch.question_types),
        context_blocks=context_blocks,
    )
    params = GenParams(temperature=float(prompt.temperature), max_tokens=prompt.max_tokens,
                       seed=rng.randint(0, 2**31 - 1))
    try:
        payload = provider.complete_json(sys_txt, user, params, "mcq_batch_v1")
    except LLMError:
        # provider failure => whole batch recorded INVALID, job continues (resumable)
        return BatchOutcome(generated=0, persisted=0, invalid=batch.count)

    persisted = invalid = 0
    for mcq in payload["questions"][:batch.count]:
        res = validate_mcq(mcq, num_options=job.num_options)
        status = MCQStatus.GENERATED if res.ok else MCQStatus.INVALID
        # §15: position randomization applies to structurally valid items only;
        # rejected/INVALID candidates keep the raw generated order as evidence.
        stored = shuffle_options(mcq, rng) if res.ok else mcq
        cand = _persist_candidate(db, job, batch, stored, model, prompt, params,
                                  status=status, chunk_map=chunk_map,
                                  citations=citations)
        db.add(MCQValidationResult(
            candidate_id=cand.id, stage="structure",
            verdict="PASS" if res.ok else "FAIL",
            details_json=json.dumps({"errors": res.errors, "warnings": res.warnings}),
        ))
        persisted += 1
        if not res.ok:
            invalid += 1
    db.commit()
    return BatchOutcome(generated=len(payload["questions"]), persisted=persisted,
                        invalid=invalid)


def _checkpoint(db: Session, job_id: int, stage: str, cursor: int,
                state: dict | None = None) -> None:
    cp = db.query(JobCheckpoint).filter_by(job_id=job_id, stage=stage).one_or_none()
    if cp is None:
        cp = JobCheckpoint(job_id=job_id, stage=stage, batch_cursor=cursor)
        db.add(cp)
    cp.batch_cursor = cursor
    cp.state_json = json.dumps(state or {})
    db.commit()


def _get_checkpoint(db: Session, job_id: int, stage: str) -> int:
    cp = db.query(JobCheckpoint).filter_by(job_id=job_id, stage=stage).one_or_none()
    return cp.batch_cursor if cp else 0


def _ensure_model(db: Session, provider: ILLMProvider) -> GenerationModel:
    m = db.query(GenerationModel).filter_by(provider_kind=provider.name, active=True) \
          .order_by(GenerationModel.id).first()
    if m:
        return m
    m = GenerationModel(name=f"{provider.name}-default", provider_kind=provider.name,
                        quantization=None, active=True)
    db.add(m)
    db.commit()
    return m


def process_job(db: Session, job_id: int, provider: ILLMProvider | None = None) -> dict:
    """Run (or resume) the MVP pipeline for one job. Returns progress summary."""
    s = get_settings()
    provider = provider or _default_provider()
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise ValueError(f"job {job_id} not found")
    if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
        return {"job_id": job_id, "status": job.status, "note": "not runnable"}

    job.status = JobStatus.RUNNING
    db.commit()

    model = db.get(GenerationModel, job.model_id) if job.model_id \
        else _ensure_model(db, provider)
    prompt = db.query(PromptTemplate).filter_by(kind="mcq_generation", version=1).one()
    rng = random.Random(job.id * 1000 + 7)   # deterministic per job → reproducible

    topics = [{"id": jt.topic_id, "weight": jt.weight,
               "importance": db.get(Topic, jt.topic_id).importance}
              for jt in job.topics]
    plan = build_plan(
        job_id=job.id, target_approved=job.target_approved,
        overgeneration_factor=job.overgeneration_factor, topics=topics,
        bloom_dist=json.loads(job.bloom_dist_json),
        difficulty_dist=json.loads(job.difficulty_dist_json),
        batch_size=s.generation_batch_size,
    )

    resume_from = _get_checkpoint(db, job.id, "generate")
    stats = {"generated": 0, "persisted": 0, "invalid": 0,
             "batches_run": 0, "batches_skipped": 0}
    for idx, batch in enumerate(plan.batches):
        if idx < resume_from:
            stats["batches_skipped"] += 1
            continue
        db.refresh(job)
        if job.status in (JobStatus.PAUSED, JobStatus.CANCELLED):
            break
        out = process_batch(db, job, batch, provider=provider, model=model,
                            prompt=prompt, rng=rng)
        stats["generated"] += out.generated
        stats["persisted"] += out.persisted
        stats["invalid"] += out.invalid
        stats["batches_run"] += 1
        _checkpoint(db, job.id, "generate", idx + 1, stats)

    total_candidates = db.query(MCQCandidate).filter_by(job_id=job.id).count()
    db.refresh(job)
    if job.status == JobStatus.RUNNING and total_candidates >= plan.candidate_target:
        job.status = JobStatus.COMPLETED
    db.commit()
    _checkpoint(db, job.id, "finish", len(plan.batches), stats)
    return {"job_id": job.id, "status": job.status,
            "candidate_target": plan.candidate_target, **stats}


def _default_provider() -> ILLMProvider:
    from backend.llm.providers import build_provider
    return build_provider()
