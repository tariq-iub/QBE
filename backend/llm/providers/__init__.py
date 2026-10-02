"""Provider registry: config-driven selection, zero vendor coupling (NFR-10)."""
from __future__ import annotations

import os

from backend.core.config import get_settings
from backend.llm.base import ILLMProvider


def build_provider(kind: str | None = None) -> ILLMProvider:
    s = get_settings()
    kind = (kind or s.llm_provider).lower()
    if kind == "mock":
        from backend.llm.providers.mock import MockProvider
        return MockProvider()
    if kind == "llamacpp":
        from backend.llm.providers.llamacpp import LocalLlamaCppProvider
        return LocalLlamaCppProvider(
            s.llamacpp_base_url,
            model=os.environ.get("AIQBE_LLAMACPP_MODEL", "local"),
            timeout=s.llm_timeout_seconds,
        )
    if kind == "ollama":
        from backend.llm.providers.ollama import OllamaProvider
        return OllamaProvider(
            s.ollama_base_url,
            model=os.environ.get("AIQBE_OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M"),
            timeout=s.llm_timeout_seconds,
        )
    if kind == "openai_compat":
        from backend.llm.providers.openai_compat import OpenAICompatibleProvider
        return OpenAICompatibleProvider(
            s.openai_compat_base_url,
            model=os.environ.get("AIQBE_OPENAI_MODEL", "local-model"),
            api_key=os.environ.get("AIQBE_OPENAI_API_KEY"),
            timeout=s.llm_timeout_seconds,
        )
    raise ValueError(f"unknown llm provider kind: {kind}")
