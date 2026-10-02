"""Ollama provider (/api/chat, format=json). Selected model must be pulled locally."""
from __future__ import annotations

import httpx

from backend.llm.base import GenParams, ILLMProvider, LLMError


class OllamaProvider(ILLMProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float = 300.0) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self.model = model

    def complete(self, system: str, user: str, params: GenParams) -> str:
        resp = self._client.post("/api/chat", json={
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "options": {
                "temperature": params.temperature,
                "top_p": params.top_p,
                "num_predict": params.max_tokens,
                "seed": params.seed,
            },
        })
        if resp.status_code != 200:
            raise LLMError(f"ollama HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()["message"]["content"]

    def complete_json(self, system, user, params, schema_name, *, attempts=2):
        resp = self._client.post("/api/chat", json={
            "model": self.model,
            "format": "json",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": params.temperature, "top_p": params.top_p,
                        "num_predict": params.max_tokens, "seed": params.seed},
        })
        if resp.status_code != 200:
            raise LLMError(f"ollama HTTP {resp.status_code}: {resp.text[:300]}")
        from backend.schemas.validate import assert_valid
        import json as _json
        payload = _json.loads(resp.json()["message"]["content"])
        assert_valid(payload, schema_name)
        return payload
