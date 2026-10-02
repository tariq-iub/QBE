"""llama.cpp server provider (local-llama-server /compatibility/* endpoints).

Uses the OpenAI-compatible surface exposed by llama.cpp's server plus native
`response_format: {"type":"json_schema", "strict":true}` for grammar-constrained
decoding — the central mechanism behind "never accept malformed JSON".
"""
from __future__ import annotations

import json

import httpx

from backend.llm.base import GenParams, ILLMProvider, LLMError
from backend.schemas.json_schemas import SCHEMAS


class LocalLlamaCppProvider(ILLMProvider):
    name = "llamacpp"

    def __init__(self, base_url: str, model: str = "local", timeout: float = 300.0) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self.model = model

    def complete(self, system: str, user: str, params: GenParams) -> str:
        resp = self._client.post("/v1/chat/completions", json={
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": params.temperature,
            "top_p": params.top_p,
            "max_tokens": params.max_tokens,
            "seed": params.seed,
        })
        if resp.status_code != 200:
            raise LLMError(f"llamacpp HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"]

    def complete_json(self, system, user, params, schema_name, *, attempts=2):
        # constrained decoding path: ask the server to enforce the schema itself
        resp = self._client.post("/v1/chat/completions", json={
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": params.temperature,
            "top_p": params.top_p,
            "max_tokens": params.max_tokens,
            "seed": params.seed,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name,
                                "schema": SCHEMAS[schema_name], "strict": True},
            },
        })
        if resp.status_code != 200:
            raise LLMError(f"llamacpp HTTP {resp.status_code}: {resp.text[:300]}")
        content = resp.json()["choices"][0]["message"]["content"]
        payload = json.loads(content)   # still validated downstream via super-style checks
        from backend.schemas.validate import assert_valid
        assert_valid(payload, schema_name)
        return payload
