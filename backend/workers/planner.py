"""Deterministic job planner (design doc 05 §2).

Distributes the over-generation target across topics proportional to
weight × importance, then slices each topic's quota into batches of size B.
Each batch carries an explicit bloom/difficulty micro-mix derived from the
job-level distributions (largest-remainder rounding → exact totals).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

BLOOM_LEVELS = ["remember", "understand", "apply", "analyze"]
DIFF_LEVELS = ["easy", "medium", "hard"]
QUESTION_TYPES = ["concept_recall", "definition", "formula_application",
                  "numerical", "conceptual_reasoning", "analysis"]


def largest_remainder(total: int, weights: dict[str, float]) -> dict[str, int]:
    """Integer allocation of `total` items proportional to normalized weights."""
    s = sum(weights.values())
    if s <= 0:
        raise ValueError("weights must sum > 0")
    raw = {k: total * v / s for k, v in weights.items()}
    base = {k: int(math.floor(v)) for k, v in raw.items()}
    rem = total - sum(base.values())
    order = sorted(raw, key=lambda k: raw[k] - base[k], reverse=True)
    for k in order[:rem]:
        base[k] += 1
    return base


@dataclass
class BatchPlan:
    topic_id: int
    seq: int
    count: int
    bloom_mix: dict[str, int]
    difficulty_mix: dict[str, int]
    question_types: list[str] = field(default_factory=list)


@dataclass
class JobPlan:
    job_id: int
    candidate_target: int
    batches: list[BatchPlan]

    def per_topic_counts(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for b in self.batches:
            out[b.topic_id] = out.get(b.topic_id, 0) + b.count
        return out


def build_plan(*, job_id: int, target_approved: int, overgeneration_factor: float,
               topics: list[dict], bloom_dist: dict[str, float],
               difficulty_dist: dict[str, float], batch_size: int = 10) -> JobPlan:
    """topics: [{id, weight?, importance?}] — effective weight = weight×importance."""
    cand_target = max(len(topics), round(target_approved * overgeneration_factor))
    eff_w = {t["id"]: float(t.get("weight") or 1.0) * float(t.get("importance") or 3)
             for t in topics}
    topic_alloc = largest_remainder(cand_target, eff_w)

    batches: list[BatchPlan] = []
    for t in topics:
        tid = t["id"]
        n = topic_alloc[tid]
        # guarantee ≥1 candidate per selected topic even at tiny targets
        if n == 0:
            n = 1
        seq = 0
        remaining = n
        while remaining > 0:
            cnt = min(batch_size, remaining)
            bloom_mix = largest_remainder(cnt, bloom_dist)
            diff_mix = largest_remainder(cnt, difficulty_dist)
            qtypes = [QUESTION_TYPES[(seq + i) % len(QUESTION_TYPES)]
                      for i in range(min(3, cnt))]
            batches.append(BatchPlan(tid, seq, cnt, bloom_mix, diff_mix, qtypes))
            seq += 1
            remaining -= cnt
    # trim/adjust total to exactly cand_target when per-topic floors inflated it
    total = sum(b.count for b in batches)
    while total > cand_target:
        last = batches[-1]
        last.count -= 1
        total -= 1
        if last.count == 0:
            batches.pop()
    return JobPlan(job_id=job_id, candidate_target=cand_target, batches=batches)
