"""Workflow status enum + legal transition map (design doc 03 §4).

The API layer refuses any status change not listed here; workers use the same
map, so lifecycle enforcement lives in exactly one place.
"""
from __future__ import annotations

from enum import StrEnum


class MCQStatus(StrEnum):
    GENERATED = "GENERATED"
    STRUCTURE_VALIDATED = "STRUCTURE_VALIDATED"
    FACT_VALIDATED = "FACT_VALIDATED"
    DEDUPLICATED = "DEDUPLICATED"
    QUALITY_CHECKED = "QUALITY_CHECKED"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_REVISION = "NEEDS_REVISION"
    DUPLICATE = "DUPLICATE"
    INVALID = "INVALID"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


# Terminal statuses cannot be left except via explicit reviewer edit which
# creates a new mcq_versions snapshot and moves NEEDS_REVISION -> GENERATED.
LEGAL_TRANSITIONS: dict[MCQStatus, set[MCQStatus]] = {
    MCQStatus.GENERATED: {
        MCQStatus.STRUCTURE_VALIDATED, MCQStatus.INVALID, MCQStatus.REJECTED,
    },
    MCQStatus.STRUCTURE_VALIDATED: {
        MCQStatus.FACT_VALIDATED, MCQStatus.INVALID, MCQStatus.LOW_CONFIDENCE,
        MCQStatus.REJECTED,
    },
    MCQStatus.FACT_VALIDATED: {
        MCQStatus.DEDUPLICATED, MCQStatus.DUPLICATE, MCQStatus.REJECTED,
        MCQStatus.LOW_CONFIDENCE,
    },
    MCQStatus.DEDUPLICATED: {
        MCQStatus.QUALITY_CHECKED, MCQStatus.REJECTED, MCQStatus.LOW_CONFIDENCE,
    },
    MCQStatus.QUALITY_CHECKED: {
        MCQStatus.PENDING_REVIEW, MCQStatus.REJECTED, MCQStatus.LOW_CONFIDENCE,
    },
    MCQStatus.PENDING_REVIEW: {
        MCQStatus.APPROVED, MCQStatus.REJECTED, MCQStatus.NEEDS_REVISION,
    },
    MCQStatus.NEEDS_REVISION: {MCQStatus.GENERATED},
    MCQStatus.LOW_CONFIDENCE: {MCQStatus.PENDING_REVIEW, MCQStatus.REJECTED},
    # terminal
    MCQStatus.APPROVED: set(),
    MCQStatus.REJECTED: set(),
    MCQStatus.DUPLICATE: set(),
    MCQStatus.INVALID: set(),
}


def can_transition(frm: MCQStatus | str, to: MCQStatus | str) -> bool:
    try:
        f, t = MCQStatus(frm), MCQStatus(to)
    except ValueError:
        return False
    return t in LEGAL_TRANSITIONS[f]
