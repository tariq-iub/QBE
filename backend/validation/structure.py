"""Structural validator — first gate after generation (design doc 05 §4).

Checks schema compliance + rules JSON Schema cannot express:
duplicate options, option-count vs job config, absurd-distractor heuristics,
answer-in-stem leakage, LaTeX bracket balance when math present.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.schemas.validate import validate_payload

MAX_PLAIN_WORDS = 6          # design FR-13; latex-heavy options get slack below
LATEX_SLACK_CHARS = 240

_ABSURD_PATTERNS = re.compile(
    r"\b(banana|pizza|unicorn|chocolate|dog|cat|monkey|lol|asdf)\b", re.I)


@dataclass
class StructureResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def normalize_option(text: str) -> str:
    return re.sub(r"[\s\W]+", "", text.lower())


def validate_mcq(mcq: dict, *, num_options: int = 4) -> StructureResult:
    errors: list[str] = []
    warnings: list[str] = []

    # per-question schema validation via the batch wrapper
    errs = validate_payload({"questions": [mcq]}, "mcq_batch_v1")
    errors.extend(errs)
    if errors:
        return StructureResult(False, errors, warnings)

    opts: list[str] = mcq["options"]
    if len(opts) != num_options:
        errors.append(f"expected {num_options} options, got {len(opts)}")

    normed = [normalize_option(o) for o in opts]
    if len(set(normed)) != len(normed):
        errors.append("duplicate/normalized-duplicate options")

    correct = mcq["correct_option"]
    if not 0 <= correct < len(opts):
        errors.append("correct_option index out of range")

    stem = mcq["question"]
    # answer-in-stem leakage: exact correct answer copied verbatim into the stem
    if correct < len(opts) and len(opts[correct]) > 3 and opts[correct].lower() in stem.lower():
        warnings.append("correct answer text appears verbatim in stem")

    # option length rule (FR-13): plain-text options ≤6 words; LaTeX allowed to be longer
    for i, o in enumerate(opts):
        has_latex = "$" in o or "\\" in o
        words = len(re.findall(r"[A-Za-z0-9]+", o))
        if has_latex:
            if len(o) > LATEX_SLACK_CHARS:
                warnings.append(f"option {i} very long even with LaTeX ({len(o)} chars)")
        elif words > MAX_PLAIN_WORDS:
            warnings.append(f"option {i} exceeds {MAX_PLAIN_WORDS} words ({words})")

    # absurd distractors
    for i, o in enumerate(opts):
        if i != correct and _ABSURD_PATTERNS.search(o):
            errors.append(f"implausible/absurd distractor at option {i}: {o!r}")

    # latex bracket balance (deeper checks land in Phase 6 notation validator)
    for src in [stem, *opts]:
        if "\\(" in src and src.count("\\(") != src.count("\\)"):
            errors.append("unbalanced inline math \\(..\\)")
        if src.count("$$") % 2 != 0:
            errors.append("unbalanced display math $$..$$")
        for a, b in (("{", "}"), ("[", "]")):
            if src.count(a) != src.count(b):
                errors.append(f"unbalanced brackets {a}{b} in: {src[:60]!r}")
                break

    # question must end with ? or contain a blank/task verb (stems like "Compute x:" OK)
    if not re.search(r"[??:]\s*$", stem.strip()):
        warnings.append("stem does not end with ?, : ")

    return StructureResult(len(errors) == 0, errors, warnings)
