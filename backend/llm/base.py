"""ILLMProvider abstraction (design doc 04 §5). Vendor-independent LLM access."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from backend.schemas.validate import assert_valid


@dataclass
class GenParams:
    temperature: float = 0.3
    top_p: float = 0.9
    top_k: int = 40
    repeat_penalty: float = 1.1
    max_tokens: int = 1500
    seed: int | None = None
    extra: dict = field(default_factory=dict)


class LLMError(RuntimeError):
    pass


class ILLMProvider(ABC):
    """All providers MUST return schema-validated dicts from complete_json.

    The base class enforces the contract: parse -> validate -> return.
    Implementations may additionally constrain decoding (grammar/GBNF/json_schema).
    """

    name: str = "abstract"

    @abstractmethod
    def complete(self, system: str, user: str, params: GenParams) -> str:
        """Raw text completion for a single prompt pair."""

    def complete_json(self, system: str, user: str, params: GenParams,
                      schema_name: str, *, attempts: int = 2) -> dict:
        last_err: Exception | None = None
        for _ in range(max(1, attempts)):
            raw = self.complete(system, user, params)
            try:
                payload = _extract_json(raw)
                assert_valid(payload, schema_name)
                return payload
            except (json.JSONDecodeError, ValueError) as exc:  # includes SchemaValidationError
                last_err = exc
        raise LLMError(f"provider {self.name}: no valid JSON after {attempts} attempts: {last_err}")

    def health(self) -> bool:
        try:
            self.complete("You are a test.", "Reply with the single word: ok",
                          GenParams(max_tokens=8))
            return True
        except Exception:
            return False


def _extract_json(raw: str) -> dict:
    """Tolerant extraction: strip fences / leading prose, then parse object."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```")[1]
        if s.startswith("json"):
            s = s[4:]
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end <= start:
        raise json.JSONDecodeError("no JSON object found", raw, 0)
    obj = json.loads(s[start:end + 1])
    if not isinstance(obj, dict):
        raise json.JSONDecodeError("top-level value is not an object", raw, 0)
    return obj
