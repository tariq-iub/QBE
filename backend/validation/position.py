"""Answer-position balancing (design doc 05 §4, FR-15).

Applied AFTER validation, BEFORE persistence: shuffles option order so the bank's
correct-answer letters trend toward uniform. Preserves correctness mapping by
permuting options together and recomputing correct_option.
"""
from __future__ import annotations

import random


def shuffle_options(mcq: dict, rng: random.Random | None = None) -> dict:
    """Return a new dict with options permuted and correct_option remapped."""
    rng = rng or random.Random()
    opts = list(mcq["options"])
    correct = int(mcq["correct_option"])
    correct_text = opts[correct]
    idx = list(range(len(opts)))
    rng.shuffle(idx)
    new_opts = [opts[i] for i in idx]
    new_correct = idx.index(correct)
    # guard: never produce identical ordering (would be pointless churn)
    if new_opts == opts and len(opts) > 1:
        new_opts[0], new_opts[-1] = new_opts[-1], new_opts[0]
        new_correct = new_opts.index(correct_text)
    out = dict(mcq)
    out["options"] = new_opts
    out["correct_option"] = new_correct
    return out


def letter_distribution(mcqs: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in mcqs:
        letter = chr(ord("A") + int(m["correct_option"]))
        counts[letter] = counts.get(letter, 0) + 1
    return counts


def position_bias_ratio(counts: dict[str, int]) -> float:
    """max/mean ratio; ~1.0 is balanced, >2.0 needs attention (job-level metric)."""
    vals = [counts.get(l, 0) for l in "ABCDE" if any(k.startswith(l) for k in counts)] or list(counts.values())
    if not vals or sum(vals) == 0:
        return 1.0
    return max(vals) / (sum(vals) / len(vals))
