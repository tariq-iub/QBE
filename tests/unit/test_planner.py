"""Planner allocation tests (§9/§30): exact totals, largest-remainder fairness."""
import pytest

from backend.workers.planner import build_plan, largest_remainder


def test_largest_remainder_sums_exactly():
    alloc = largest_remainder(100, {"a": 0.25, "b": 0.30, "c": 0.30, "d": 0.15})
    assert sum(alloc.values()) == 100
    assert alloc == {"a": 25, "b": 30, "c": 30, "d": 15}


def test_largest_remainder_handles_indivisible():
    alloc = largest_remainder(10, {"x": 1, "y": 1, "z": 1})
    assert sum(alloc.values()) == 10
    assert sorted(alloc.values()) == [3, 3, 4]      # fair split of remainder


def test_largest_remainder_rejects_zero_weights():
    with pytest.raises(ValueError):
        largest_remainder(5, {"a": 0.0})


BLOOM = {"remember": 0.25, "understand": 0.30, "apply": 0.30, "analyze": 0.15}
DIFF = {"easy": 0.30, "medium": 0.50, "hard": 0.20}


def _plan(target=100, topics=None, batch_size=10, factor=1.35):
    topics = topics or [{"id": 1}, {"id": 2}, {"id": 3}]
    return build_plan(job_id=1, target_approved=target, overgeneration_factor=factor,
                      topics=topics, bloom_dist=BLOOM, difficulty_dist=DIFF,
                      batch_size=batch_size)


def test_candidate_target_matches_overgeneration():
    plan = _plan(target=100)
    assert plan.candidate_target == 135
    assert sum(b.count for b in plan.batches) == 135


def test_weighted_topic_allocation():
    plan = _plan(target=100, topics=[{"id": 1, "weight": 3.0}, {"id": 2, "weight": 1.0}])
    per = plan.per_topic_counts()
    assert sum(per.values()) == 135
    assert per[1] > per[2] and per[1] >= 3 * per[2] - 3   # roughly 3:1


def test_every_selected_topic_gets_at_least_one():
    plan = _plan(target=1, topics=[{"id": i} for i in range(1, 6)])
    assert set(plan.per_topic_counts()) == {1, 2, 3, 4, 5}
    assert sum(b.count for b in plan.batches) == plan.candidate_target


def test_batches_never_exceed_max_and_mixes_sum():
    plan = _plan(target=200)
    for b in plan.batches:
        assert 1 <= b.count <= 10
        assert sum(b.bloom_mix.values()) == b.count
        assert sum(b.difficulty_mix.values()) == b.count
        assert len(b.question_types) == min(3, b.count)


def test_trim_keeps_micro_mixes_consistent():
    # odd target + floors can force trimming; mixes must still sum to count
    plan = _plan(target=7, topics=[{"id": 1, "weight": 10}, {"id": 2, "weight": 0.01},
                                   {"id": 3, "weight": 0.01}], batch_size=10)
    assert sum(b.count for b in plan.batches) == plan.candidate_target
    for b in plan.batches:
        assert sum(b.bloom_mix.values()) == b.count
        assert sum(b.difficulty_mix.values()) == b.count
