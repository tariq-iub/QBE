"""Strict JSON schemas for LLM structured output (design doc 05 §3).

Single source of truth shared by: prompt instructions, provider grammar
constraints, and the structural validator. "Never accept malformed JSON."
"""
from __future__ import annotations

MCQ_BATCH_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "MCQBatch",
    "type": "object",
    "additionalProperties": False,
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 30,
            "items": {"$ref": "#/$defs/MCQ"},
        }
    },
    "$defs": {
        "MCQ": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "question", "options", "correct_option", "explanation",
                "bloom_level", "difficulty", "evidence_refs",
            ],
            "properties": {
                "question": {"type": "string", "minLength": 12, "maxLength": 600},
                "options": {
                    "type": "array", "minItems": 4, "maxItems": 5,
                    "items": {"type": "string", "minLength": 1, "maxLength": 240},
                },
                "correct_option": {"type": "integer", "minimum": 0, "maximum": 4},
                "explanation": {"type": "string", "minLength": 10, "maxLength": 1200},
                "subtopic": {"type": ["string", "null"], "maxLength": 120},
                "difficulty": {"enum": ["easy", "medium", "hard"]},
                "bloom_level": {"enum": ["remember", "understand", "apply", "analyze"]},
                "question_type": {
                    "enum": ["concept_recall", "definition", "formula_application",
                             "numerical", "conceptual_reasoning", "analysis"],
                },
                # grounding: every factual question MUST cite evidence chunk ids
                "evidence_refs": {
                    "type": "array", "minItems": 1,
                    "items": {"type": "integer", "minimum": 1},
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        }
    },
}

VERDICT_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "VerifierVerdict",
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "reason", "confidence"],
    "properties": {
        "verdict": {"enum": ["PASS", "FAIL", "UNCERTAIN"]},
        "reason": {"type": "string", "minLength": 5, "maxLength": 800},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_ref": {"type": ["integer", "null"], "minimum": 1},
    },
}

CLASSIFY_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "DifficultyBloomClassification",
    "type": "object",
    "additionalProperties": False,
    "required": ["difficulty", "bloom_level"],
    "properties": {
        "difficulty": {"enum": ["easy", "medium", "hard"]},
        "bloom_level": {"enum": ["remember", "understand", "apply", "analyze"]},
        "rationale": {"type": ["string", "null"], "maxLength": 400},
    },
}

# knowledge extraction / duplicate judge schemas arrive with Phases 3/7; keep a registry
SCHEMAS: dict[str, dict] = {
    "mcq_batch_v1": MCQ_BATCH_SCHEMA,
    "verifier_verdict_v1": VERDICT_SCHEMA,
    "classify_v1": CLASSIFY_SCHEMA,
}
