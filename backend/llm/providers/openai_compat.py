"""OpenAI-compatible provider (vLLM / LM Studio / internal gateways).

Also the escape hatch for *later* migration to a bigger server: point base_url
at any OpenAI-compatible endpoint; no application code changes (NFR-10).
"""
from __future__ import annotations

import httpx

from backend.llm.base import GenParams, ILLMProvider, LLMError
from backend.schemas.json_schemas import SCHEMAS


class OpenAICompatibleProvider(ILLMProvider):
    name = "openai_compat"

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 timeout: float = 300.0) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout,
                                    headers=headers)
        self.model = model

    def complete(self, system: str, user: str, params: GenParams) -> str:
        resp = self._client.post("/chat/completions", json={
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": params.temperature,
            "top_p": params.top_p,
            "max_tokens": params.max_tokens,
            "seed": params.seed,
        })
        if resp.status_code != 200:
            raise LLMError(f"openai_compat HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"]

    def complete_json(self, system, user, params, schema_name, *, attempts=2):
        resp = self._client.post("/chat/completions", json={
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
            raise LLMError(f"openai_compat HTTP {resp.status_code}: {resp.text[:300]}")
        import json as _json
        from backend.schemas.validate import assert_valid
        payload = _json.loads(resp.json()["choices"][0]["message"]["content"])
        assert_valid(payload, schema_name)
        return payload
