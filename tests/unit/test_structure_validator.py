"""Structural validator tests — positives, negatives, LaTeX handling (FR-13/§19)."""
from backend.validation.structure import validate_mcq


def good_mcq(**over):
    mcq = {
        "question": "Which law relates force to acceleration?",
        "options": ["Newton's first law", "Newton's second law",
                    "Newton's third law", "Law of gravitation"],
        "correct_option": 1,
        "explanation": "Newton's second law states F = ma, relating net force to "
                       "mass times acceleration.",
        "bloom_level": "remember",
        "difficulty": "easy",
        "question_type": "concept_recall",
        "evidence_refs": [1],
        "confidence": 0.94,
    }
    mcq.update(over)
    return mcq


def test_valid_mcq_passes():
    res = validate_mcq(good_mcq())
    assert res.ok, res.errors


def test_missing_evidence_rejected_by_schema():
    res = validate_mcq(good_mcq(evidence_refs=[]))
    assert not res.ok
    assert any("evidence_refs" in e for e in res.errors)


def test_duplicate_options_rejected():
    res = validate_mcq(good_mcq(options=["newton's second law", "Newtons Second Law",
                                         "third law here", "gravitation law"]))
    assert not res.ok
    assert any("duplicate" in e for e in res.errors)


def test_wrong_option_count_for_job_config():
    res = validate_mcq(good_mcq(), num_options=5)
    assert not res.ok
    assert any("expected 5 options" in e for e in res.errors)


def test_absurd_distractor_rejected():
    res = validate_mcq(good_mcq(options=["first law name", "second law name",
                                         "banana", "gravity law"]))
    assert not res.ok
    assert any("absurd" in e for e in res.errors)


def test_correct_answer_absurd_is_not_flagged_as_distractor():
    # absurdity check applies to distractors only; schema still passes the item
    res = validate_mcq(good_mcq(options=["law one", "banana split", "law three",
                                         "law four"], correct_option=1))
    assert res.ok or all("absurd" not in e for e in res.errors)


def test_long_plain_option_warns_but_does_not_fail():
    res = validate_mcq(good_mcq(options=["a b c d e f g h", "second", "third", "fourth"]))
    assert res.ok
    assert any("exceeds 6 words" in w for w in res.warnings)


def test_latex_option_not_penalised_for_length():
    latex_opt = r"\(F = m a\cos(\theta) + \mu m g \sin(\theta)\)"
    res = validate_mcq(good_mcq(
        question=r"A block on an incline satisfies which equation: \(F=?\)",
        options=[latex_opt, r"\(F = mg\)", r"\(F = mv^2/r\)", r"\(F = 0\)"],
        correct_option=0))
    assert res.ok, res.errors
    assert not any("exceeds 6 words" in w for w in res.warnings)


def test_unbalanced_latex_rejected():
    res = validate_mcq(good_mcq(question=r"Compute \(x^2 + y^2 where x=3?"))
    assert not res.ok
    assert any("unbalanced inline math" in e for e in res.errors)

    res2 = validate_mcq(good_mcq(question="Evaluate $$ \\int_0^1 x\\,dx and more"))
    assert not res2.ok
    assert any("unbalanced display" in e for e in res2.errors)


def test_unbalanced_inline_math_delimiter_rejected():
    # option contains "\(" opener without a matching "\)"
    res = validate_mcq(good_mcq(options=[r"\(H_2O", "CO_2 formula", "Na+ ion", "SO4 ion"]))
    assert not res.ok
    assert any("unbalanced inline math" in e for e in res.errors)


def test_unbalanced_braces_rejected():
    # no inline-math delimiters at all — pure brace imbalance must be caught
    res = validate_mcq(good_mcq(options=["H_{2}O and {x", "CO_2", "Na+", "SO4"]))
    assert not res.ok
    assert any("unbalanced brackets" in e for e in res.errors)


def test_answer_in_stem_leakage_warns():
    res = validate_mcq(good_mcq(
        question="Newton's second law relates force to acceleration. Which law is this: "
                 "Newton's second law?",
        options=["first law x", "Newton's second law", "third law y", "gravitation z"]))
    assert res.ok
    assert any("verbatim in stem" in w for w in res.warnings)


def test_bad_enum_fails_schema():
    res = validate_mcq(good_mcq(bloom_level="create"))
    assert not res.ok


def test_confidence_out_of_range_fails():
    res = validate_mcq(good_mcq(confidence=1.7))
    assert not res.ok
