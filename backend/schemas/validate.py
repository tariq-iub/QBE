"""Schema validation helper used by providers AND the structural validator.

Guarantees: any object returned by llm.complete_json(schema_name=...) has
already passed jsonschema validation; validators re-check independently so a
misconfigured provider cannot smuggle malformed output into the pipeline.
"""
from __future__ import annotations

from jsonschema import Draft202012Validator

from backend.schemas.json_schemas import SCHEMAS


class SchemaValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_payload(payload: dict, schema_name: str) -> list[str]:
    """Return list of error strings (empty == valid)."""
    schema = SCHEMAS[schema_name]
    validator = Draft202012Validator(schema)
    return [
        f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(payload), key=lambda x: list(x.path))
    ]


def assert_valid(payload: dict, schema_name: str) -> None:
    errors = validate_payload(payload, schema_name)
    if errors:
        raise SchemaValidationError(errors)
