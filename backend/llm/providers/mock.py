"""Mock provider — deterministic, offline.

Used for: CI tests of the whole pipeline without GPU, and as the Phase-1
harness smoke-test target (verifies plumbing before real models are attached).
Emits schema-valid MCQ batches derived from the prompt's knowledge pack text.
"""
from __future__ import annotations

import json
import re

from backend.llm.base import GenParams, ILLMProvider


class MockProvider(ILLMProvider):
    name = "mock"

    def __init__(self, *, fail_json: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail_json = fail_json   # test hook: force invalid output

    def complete(self, system: str, user: str, params: GenParams) -> str:
        self.calls.append((system, user))
        if self._fail_json:
            return "I cannot produce JSON today."
        n = _requested_count(user)
        topic = _extract_field(user, "Topic:") or "General"
        qs = []
        for i in range(n):
            qs.append({
                "question": f"[{topic}] Sample conceptual question {i + 1} about the topic material?",
                "options": ["first plausible option", "second plausible option",
                            "third plausible option", "fourth plausible option"],
                "correct_option": i % 4,
                "explanation": f"Deterministic mock explanation {i + 1} grounded in the supplied context.",
                "subtopic": None,
                "difficulty": ["easy", "medium", "hard"][i % 3],
                "bloom_level": ["remember", "understand", "apply", "analyze"][i % 4],
                "question_type": "conceptual_reasoning",
                "evidence_refs": [1],
                "confidence": 0.9,
            })
        return json.dumps({"questions": qs})


def _requested_count(user: str) -> int:
    m = re.search(r"Generate exactly (\d+) questions", user)
    return max(1, min(30, int(m.group(1)))) if m else 5


def _extract_field(user: str, label: str) -> str | None:
    m = re.search(re.escape(label) + r"\s*(.+)", user)
    return m.group(1).strip() if m else None
