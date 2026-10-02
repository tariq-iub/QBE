"""ILLMProvider abstraction (design doc 04 §5). Vendor-independent LLM access."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from backend.schemas.validate import SchemaValidationError, assert_valid


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
            except SchemaValidationError as exc:
                # malformed-for-our-schema output: retry with the provider; if the
                # budget is exhausted the batch is rejected upstream. Never let a
                # schema-invalid dict escape this method (§52 "never accept
                # malformed JSON").
                last_err = exc
            except json.JSONDecodeError as exc:
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
    """Tolerant extraction: strip fences / leading prose, then parse object.

    Robust against prose before/after the JSON and even stray '{...}' fragments
    in the surrounding text: every candidate opening brace is tried until one
    yields a top-level object.
    """
    s = raw.strip()
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) > 1:
            s = parts[1]
            if s.startswith("json"):
                s = s[4:]
    start = 0
    last_err: Exception | None = None
    while True:
        start = s.find("{", start)
        if start == -1:
            break
        end = s.rfind("}")
        if end <= start:
            break
        try:
            obj = json.loads(s[start:end + 1])
            if isinstance(obj, dict):
                return obj
            last_err = json.JSONDecodeError("top-level value is not an object", raw, 0)
        except json.JSONDecodeError as exc:
            last_err = exc
        start += 1
    raise last_err or json.JSONDecodeError("no JSON object found", raw, 0)
