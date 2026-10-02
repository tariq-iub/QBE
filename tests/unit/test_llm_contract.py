"""ILLMProvider contract tests: schema enforcement, tolerant parsing, retry (NFR/§52)."""
import json

import pytest

from backend.llm.base import GenParams, ILLMProvider, LLMError, _extract_json
from backend.llm.providers.mock import MockProvider
from backend.schemas.validate import SchemaValidationError


def _user_prompt(n=3):
    return f"Topic: Newton's Laws\nGenerate exactly {n} questions for this topic."


def test_mock_provider_returns_schema_valid_payload():
    p = MockProvider()
    out = p.complete_json("sys", _user_prompt(4), GenParams(), "mcq_batch_v1")
    assert len(out["questions"]) == 4
    for q in out["questions"]:
        assert set(q) >= {"question", "options", "correct_option", "evidence_refs"}


def test_invalid_output_retried_then_raises():
    p = MockProvider(fail_json=True)
    with pytest.raises(LLMError):
        p.complete_json("sys", _user_prompt(), GenParams(), "mcq_batch_v1", attempts=3)
    assert len(p.calls) == 3          # retried up to the attempt budget


def test_schema_violation_is_not_smuggled_through():
    class BadJSON(MockProvider):
        def complete(self, system, user, params):
            self.calls.append((system, user))
            return json.dumps({"questions": [{"question": "short", "options": ["a", "b"],
                                              "correct_option": 9}]})
    p = BadJSON()
    # complete_json NEVER returns schema-invalid data; failure surfaces as LLMError
    with pytest.raises(LLMError):
        p.complete_json("sys", _user_prompt(), GenParams(), "mcq_batch_v1", attempts=1)
    with pytest.raises(LLMError):
        BadJSON().complete_json("sys", _user_prompt(), GenParams(), "mcq_batch_v1", attempts=2)


def test_extract_json_handles_fences_and_prose():
    obj = {"questions": []}
    for raw in [json.dumps(obj),
                f"```json\n{json.dumps(obj)}\n```",
                f"Sure! Here is the result:\n{json.dumps(obj)}\nHope that helps.",
                f"Here {{not json}} then {json.dumps(obj)} trailing {{brace"]:
        parsed = _extract_json(raw)
        assert isinstance(parsed, dict)


def test_extract_json_rejects_non_object():
    with pytest.raises(json.JSONDecodeError):
        _extract_json("[1, 2, 3]")
    with pytest.raises(json.JSONDecodeError):
        _extract_json("no braces at all")


def test_verdict_schema_enforced():
    class Verdict(MockProvider):
        def complete(self, system, user, params):
            self.calls.append((system, user))
            return json.dumps({"verdict": "PASS", "reason": "evidence supports answer",
                               "confidence": 0.8, "evidence_ref": 2})
    out = Verdict().complete_json("s", "u", GenParams(), "verifier_verdict_v1")
    assert out["verdict"] == "PASS"

    class BadVerdict(Verdict):
        def complete(self, system, user, params):
            self.calls.append((system, user))
            return json.dumps({"verdict": "MAYBE", "reason": "x", "confidence": 0.5})
    # complete_json's public contract: provider failures are always LLMError,
    # never a returned-but-invalid dict and never a raw validation exception.
    with pytest.raises(LLMError):
        BadVerdict().complete_json("s", "u", GenParams(), "verifier_verdict_v1", attempts=1)
    with pytest.raises(LLMError):
        BadVerdict().complete_json("s", "u", GenParams(), "verifier_verdict_v1", attempts=2)
