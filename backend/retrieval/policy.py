"""Domain policy loading/validation (Phase 4, design doc 05 §8).

The policy file is admin-owned config; a malformed one must fail closed
(internet retrieval disabled) rather than fall back to "allow everything".
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

import yaml

QUALITY_TIERS = ("authoritative", "educational", "general")
_TIER_RANK = {t: i for i, t in enumerate(QUALITY_TIERS)}  # lower rank = stricter


class PolicyError(ValueError):
    pass


@dataclass
class DomainPolicy:
    enabled: bool = False
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    priority: dict[str, int] = field(default_factory=dict)
    min_source_quality: str = "educational"
    max_bytes_per_fetch: int = 2_000_000
    max_pages_per_topic: int = 3
    timeout_seconds: float = 15.0
    user_agent: str = "AIQBE-AcademicResearchBot/0.1"
    search_provider: str | None = None
    search_endpoint: str | None = None
    search_api_key_env: str | None = None
    seed_urls: dict[int, list[str]] = field(default_factory=dict)  # topic_id -> urls

    def tier_rank(self) -> int:
        return _TIER_RANK[self.min_source_quality]

    def domain_allowed(self, host: str) -> bool:
        """Exact-or-suffix match against allowlist; blocklist always wins."""
        h = host.lower()
        if any(_domain_match(h, d) for d in self.blocked_domains):
            return False
        return any(_domain_match(h, d) for d in self.allowed_domains)

    def priority_for(self, host: str) -> int:
        h = host.lower()
        best = 50                          # unlisted-but-allowed default
        for pattern, p in self.priority.items():
            if _domain_match(h, pattern):
                best = min(best, int(p))
        return best


def _domain_match(host: str, pattern: str) -> bool:
    p = pattern.lower().lstrip(".")
    if p.startswith("*"):
        return fnmatch.fnmatch(host, p.lstrip("*")) or host.endswith(p[1:])
    return host == p or host.endswith("." + p)


def load_policy(path: str | Path) -> DomainPolicy:
    """Load + validate the YAML policy. Raises PolicyError on malformed input."""
    p = Path(path)
    if not p.exists():
        raise PolicyError(f"policy file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PolicyError(f"invalid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise PolicyError("policy root must be a mapping")

    allowed = raw.get("allowed_domains") or []
    blocked = raw.get("blocked_domains") or []
    for name, lst in (("allowed_domains", allowed), ("blocked_domains", blocked)):
        if not isinstance(lst, list) or not all(isinstance(x, str) for x in lst):
            raise PolicyError(f"{name} must be a list of strings")

    mq = raw.get("min_source_quality", "educational")
    if mq not in QUALITY_TIERS:
        raise PolicyError(f"min_source_quality must be one of {QUALITY_TIERS}")

    pr = raw.get("priority") or {}
    if not isinstance(pr, dict) or not all(isinstance(v, (int, float)) for v in pr.values()):
        raise PolicyError("priority must map domain -> number")

    search = raw.get("search") or {}
    seeds_raw = raw.get("seed_urls") or {}
    if not isinstance(search, dict):
        raise PolicyError("search must be a mapping")
    if not isinstance(seeds_raw, dict) or any(
            not isinstance(v, list) for v in seeds_raw.values()):
        raise PolicyError("seed_urls must map topic_id -> list of URLs")
    try:
        seeds = {int(k): [str(u) for u in v] for k, v in seeds_raw.items()}
    except (TypeError, ValueError) as e:
        raise PolicyError(f"seed_urls keys must be integer topic ids: {e}") from e

    return DomainPolicy(
        enabled=bool(raw.get("enabled", False)),
        allowed_domains=[str(x) for x in allowed],
        blocked_domains=[str(x) for x in blocked],
        priority={str(k): v for k, v in pr.items()},
        min_source_quality=str(mq),
        max_bytes_per_fetch=int(raw.get("max_bytes_per_fetch", 2_000_000)),
        max_pages_per_topic=int(raw.get("max_pages_per_topic", 3)),
        timeout_seconds=float(raw.get("timeout_seconds", 15)),
        user_agent=str(raw.get("user_agent", "AIQBE-AcademicResearchBot/0.1")),
        search_provider=search.get("provider"),
        search_endpoint=search.get("endpoint"),
        search_api_key_env=search.get("api_key_env"),
        seed_urls=seeds,
    )
