"""Lifecycle transition-map tests (design doc 03 §4, §26)."""
import pytest

from backend.models.enums import MCQStatus, can_transition


def test_happy_path_forward_transitions():
    path = [MCQStatus.GENERATED, MCQStatus.STRUCTURE_VALIDATED, MCQStatus.FACT_VALIDATED,
            MCQStatus.DEDUPLICATED, MCQStatus.QUALITY_CHECKED, MCQStatus.PENDING_REVIEW,
            MCQStatus.APPROVED]
    for a, b in zip(path, path[1:]):
        assert can_transition(a, b), f"{a} -> {b} must be legal"


def test_no_skipping_stages():
    assert not can_transition(MCQStatus.GENERATED, MCQStatus.APPROVED)
    assert not can_transition(MCQStatus.STRUCTURE_VALIDATED, MCQStatus.PENDING_REVIEW)
    assert not can_transition(MCQStatus.GENERATED, MCQStatus.PENDING_REVIEW)


def test_human_only_approval_boundary():
    # only PENDING_REVIEW may reach APPROVED — no automated stage has that edge
    for src in MCQStatus:
        if src is not MCQStatus.PENDING_REVIEW:
            assert not can_transition(src, MCQStatus.APPROVED)


def test_terminal_states_are_final():
    for t in (MCQStatus.APPROVED, MCQStatus.REJECTED, MCQStatus.DUPLICATE, MCQStatus.INVALID):
        for dst in MCQStatus:
            assert not can_transition(t, dst)


def test_revision_loop():
    assert can_transition(MCQStatus.PENDING_REVIEW, MCQStatus.NEEDS_REVISION)
    assert can_transition(MCQStatus.NEEDS_REVISION, MCQStatus.GENERATED)
    assert not can_transition(MCQStatus.NEEDS_REVISION, MCQStatus.APPROVED)


def test_unknown_status_string_rejected():
    assert not can_transition("GENERATED", "NOT_A_STATUS")
    assert not can_transition("BOGUS", "APPROVED")


def test_string_form_works():
    assert can_transition("GENERATED", "STRUCTURE_VALIDATED")
