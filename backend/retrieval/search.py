"""Academic web search adapter (Phase 4).

Pluggable, self-host-friendly result discovery. v1 ships:
  * NullSearch      — no engine; only admin-curated seed URLs are used;
  * SearxngSearch   — self-hosted metasearch (no third-party API keys);
  * OpenAIBackendSearch — optional hook for a local retrieval-enabled server.

All engines return candidate URLs which MUST still pass the fetch guard —
a search result is never trusted as an approved source by itself (§6).
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from backend.retrieval.policy import DomainPolicy


@dataclass
class SearchResult:
    url: str
    title: str | None = None
    snippet: str | None = None


class ISearchProvider(ABC):
    name = "abstract"

    @abstractmethod
    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]: ...


class NullSearch(ISearchProvider):
    """Deterministic offline default: curated seeds only, zero external calls."""
    name = "null"

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        return []


class SearxngSearch(ISearchProvider):
    """Self-hosted SearXNG JSON API (https://docs.searxng.org/dev/search_api.html)."""
    name = "searxng"

    def __init__(self, endpoint: str, timeout: float = 10.0):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        params = {"q": query, "format": "json", "categories": "science,it"}
        try:
            r = httpx.get(f"{self.endpoint}/search", params=params,
                          timeout=self.timeout)
            r.raise_for_status()
            results = r.json().get("results", [])[:limit]
        except (httpx.HTTPError, ValueError):
            return []          # search failure degrades to seeds-only, never crashes job
        return [SearchResult(url=x.get("url", ""), title=x.get("title"),
                             snippet=x.get("content"))
                for x in results if x.get("url", "").startswith("https://")]


def make_search_provider(policy: DomainPolicy) -> ISearchProvider:
    kind = (policy.search_provider or "").lower()
    if kind == "searxng" and policy.search_endpoint:
        return SearxngSearch(policy.search_endpoint)
    if kind == "openai_compat" and policy.search_endpoint:
        # Future: point at a local server exposing /search-style tooling.
        return SearxngSearch(policy.search_endpoint)   # same JSON contract for now
    return NullSearch()


def filter_results(results: list[SearchResult], policy: DomainPolicy,
                   *, limit: int = 3) -> list[SearchResult]:
    """Pre-filter by domain policy + min-quality tier before any network call."""
    from backend.retrieval.fetchguard import validate_url, FetchDenied
    out: list[SearchResult] = []
    seen: set[str] = set()
    for res in results:
        if res.url in seen:
            continue
        try:
            host, _ip = validate_url(res.url, policy)
        except FetchDenied:
            continue
        if policy.priority_for(host) > 40:     # below min quality bar
            continue
        seen.add(res.url)
        out.append(res)
        if len(out) >= limit:
            break
    return out
