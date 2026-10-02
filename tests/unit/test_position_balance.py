"""Option-shuffle correctness preservation + bias metrics (§15)."""
import random

from backend.validation.position import letter_distribution, position_bias_ratio, shuffle_options


BASE = {
    "question": "SI unit of force?",
    "options": ["joule", "newton", "watt", "pascal"],
    "correct_option": 1,
    "explanation": "Force is measured in newtons.",
}


def test_shuffle_preserves_correct_mapping():
    rng = random.Random(42)
    for _ in range(200):
        out = shuffle_options(BASE, rng)
        assert out["options"][out["correct_option"]] == "newton"
        assert sorted(out["options"]) == sorted(BASE["options"])
        assert out["options"] != BASE["options"]      # always permuted


def test_shuffle_returns_new_dict_original_untouched():
    out = shuffle_options(BASE, random.Random(1))
    assert BASE["options"] == ["joule", "newton", "watt", "pascal"]
    assert BASE["correct_option"] == 1
    assert out is not BASE


def test_five_options_supported():
    mcq = dict(BASE, options=["a1", "b2", "c3", "d4", "e5"], correct_option=3)
    out = shuffle_options(mcq, random.Random(7))
    assert out["options"][out["correct_option"]] == "d4"
    assert len(out["options"]) == 5


def test_letter_distribution_balances_after_shuffle():
    rng = random.Random(3)
    items = [shuffle_options(dict(BASE, correct_option=i % 4), rng) for i in range(400)]
    dist = letter_distribution(items)
    assert set(dist) <= {"A", "B", "C", "D"}
    assert sum(dist.values()) == 400
    assert position_bias_ratio(dist) < 1.5            # approximately uniform


def test_bias_ratio_detects_skew():
    assert position_bias_ratio({"A": 100}) == 1.0
    skewed = {"A": 80, "B": 10, "C": 5, "D": 5}
    assert position_bias_ratio(skewed) > 2.0
