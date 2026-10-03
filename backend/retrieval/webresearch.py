"""Controlled internet research orchestrator (Phase 4, design doc 05 §8).

Flow per topic: seed URLs (+ optional search results) -> fetch guard ->
sanitize -> quality tiering -> reuse Phase-3 ingestion pipeline with
origin="web" provenance. Web evidence then merges into Knowledge Packs
automatically because packs read the same chunk/vector store (§7/§8).

Governance (§36): only allowlisted domains; license notes recorded when known;
synthesis-not-copying is enforced downstream by generation prompts; every
fetch failure degrades gracefully (topic keeps local-only grounding).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.models.entities import AcademicSource, Topic
from backend.rag import ingest
from backend.retrieval.fetchguard import FetchDenied, FetchedContent, fetch, validate_url
from backend.retrieval.policy import _TIER_RANK, DomainPolicy, load_policy
from backend.retrieval.sanitize import sanitize_html
from backend.retrieval.search import ISearchProvider, SearchResult, filter_results, make_search_provider

log = logging.getLogger("aiqbe.research")


@dataclass
class ResearchOutcome:
    topic_id: int
    fetched: list[dict] = field(default_factory=list)     # per-url status rows
    skipped_reasons: dict[str, str] = field(default_factory=dict)
    injection_quarantined: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ingested_count(self) -> int:
        return sum(1 for f in self.fetched if f.get("ingested"))


# ---------------------------------------------------------------------------
# Source quality governance (§6 MinimumSourceQuality)
# ---------------------------------------------------------------------------

_AUTHORITATIVE_HINTS = ("nist.gov", "ocw.mit.edu", "openstax.org", ".edu")
_EDUCATIONAL_HINTS = ("libretexts.org", "wikipedia.org", "britannica.com",
                      "khanacademy.org")


def classify_quality_tier(host: str, *, injection_suspect: bool = False) -> str:
    """Tier from domain character; injection suspicion demotes one level."""
    h = host.lower()
    if any(h.endswith(suf.lstrip(".")) or suf in h for suf in _AUTHORITATIVE_HINTS):
        tier = "authoritative"
    elif any(suf in h for suf in _EDUCATIONAL_HINTS):
        tier = "educational"
    else:
        tier = "general"
    if injection_suspect:
        order = ["authoritative", "educational", "general"]
        i = order.index(tier)
        tier = order[min(i + 1, len(order) - 1)]
    return tier


def load_domain_policy(path: str | None = None) -> DomainPolicy | None:
    """Fail-closed policy loader: missing/broken file => no web retrieval."""
    s = get_settings()
    try:
        return load_policy(path or s.domain_policy_path)
    except Exception as e:                        # noqa: BLE001 — fail closed
        log.warning("domain policy unavailable (%s); internet retrieval disabled", e)
        return None


# ---------------------------------------------------------------------------
# Core orchestration
# ---------------------------------------------------------------------------

def _candidate_urls(topic: Topic, policy: DomainPolicy,
                    search: ISearchProvider) -> tuple[list[str], dict[str, str]]:
    """Returns (approved candidate URLs, {denied_url: reason}).

    Denied-but-requested URLs are *returned as candidates too* (appended after
    approved ones) so the main loop records an auditable skip reason for each;
    they can never be fetched because the loop re-validates before any network
    call. This keeps non-allowlisted seeds out of the per-topic fetch budget
    while still surfacing them in job reports (§43 observability).
    """
    urls: list[str] = []
    denied: dict[str, str] = {}
    urls.extend(policy.seed_urls.get(topic.id, []))
    if policy.search_endpoint:
        q = f"{topic.name} ({topic.subject.name if topic.subject else ''}) academic"
        results = filter_results(search.search(q, limit=5), policy,
                                 limit=policy.max_pages_per_topic)
        urls.extend(r.url for r in results)
    # de-dupe preserving order
    seen: set[str] = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    # Budget is per *fetched page*: drop URLs whose host fails the static
    # policy gate (allowlist/blocklist/scheme) before applying
    # max_pages_per_topic, so one junk seed URL can never starve approved
    # sources of their fetch budget. DNS pinning still happens at fetch time.
    allowed = []
    for u in out:
        try:
            validate_url(u, policy, resolve=False)
        except FetchDenied as e:
            denied[u] = str(e)
            continue
        allowed.append(u)
    return (allowed + list(denied))[:policy.max_pages_per_topic + len(denied)], denied


def research_topic(db: Session, topic_id: int, *, policy: DomainPolicy | None = None,
                   client=None, search: ISearchProvider | None = None,
                   storage_dir: str | None = None) -> ResearchOutcome:
    """Fetch+ingest approved web material for one topic. Never raises on
    individual URL failures; job-level callers see a summary instead."""
    s = get_settings()
    if not s.internet_retrieval_enabled:
        return ResearchOutcome(topic_id=topic_id,
                               skipped_reasons={"*": "internet retrieval disabled globally"})
    policy = policy or load_domain_policy()
    if policy is None or not policy.enabled:
        return ResearchOutcome(topic_id=topic_id,
                               skipped_reasons={"*": "no valid enabled domain policy"})
    topic = db.get(Topic, topic_id)
    if topic is None:
        return ResearchOutcome(topic_id=topic_id, errors=[f"topic {topic_id} not found"])

    outcome = ResearchOutcome(topic_id=topic_id)
    search = search or make_search_provider(policy)
    candidates, pre_denied = _candidate_urls(topic, policy, search)
    for url in candidates:
        row: dict = {"url": url, "ingested": False}

        # Governance-first ordering (§6/§35/§36): a URL whose host is not on
        # the approved allowlist is DENIED before any network attempt — it can
        # never be fetched, ingested, or reach a Knowledge Pack. The fetch
        # guard re-checks this (with DNS pinning) at request time; recording
        # the denial here keeps skip reasons honest and auditable.
        try:
            validate_url(url, policy, resolve=False)
        except FetchDenied as e:
            row["denied"] = str(e)
            outcome.skipped_reasons[url] = str(e)
            outcome.fetched.append(row)
            continue

        try:
            fc = fetch(url, policy, client=client)
        except FetchDenied as e:
            row["denied"] = str(e)
            outcome.skipped_reasons[url] = str(e)
            outcome.fetched.append(row)
            continue
        except Exception as e:                    # noqa: BLE001 — network flakiness
            row["error"] = str(e)
            outcome.errors.append(f"{url}: {e}")
            outcome.fetched.append(row)
            continue

        if fc.content_type == "application/pdf":
            # PDFs go through the same extractor path as uploads after a
            # byte-level sanity check; sanitization happens inside textproc.
            text = None
            title = None
            payload = fc.content
            kind = "pdf"
        else:
            sd = sanitize_html(fc.content)
            if sd.injection_suspect:
                outcome.injection_quarantined.append(url)
                log.warning("prompt-injection markers redacted in %s", url)
            text, title, payload, kind = sd.text, sd.title, \
                sd.text.encode("utf-8"), "html"

        host = validate_url(fc.final_url, policy)[0]
        raw_tier = classify_quality_tier(host)
        quarantined = url in outcome.injection_quarantined
        # Quarantine semantics (§35): suspected-injection pages are NOT dropped
        # (the sanitizer already redacted the instruction-like lines, so the
        # stored payload is defanged), but they are held to a stricter bar —
        # they must clear min_source_quality on their own merits *before*
        # demotion. A demoted tier only labels provenance for ranking/review.
        if _TIER_RANK[raw_tier] > policy.tier_rank():
            row["denied"] = (f"quality tier {raw_tier!r} below minimum "
                             f"{policy.min_source_quality!r}")
            outcome.skipped_reasons[url] = row["denied"]
            outcome.fetched.append(row)
            continue
        tier = classify_quality_tier(host, injection_suspect=quarantined)

        fname = f"web_{host.replace('.', '_')}.{'pdf' if kind == 'pdf' else 'txt'}"
        try:
            res = ingest_bytes_web(db, data=payload, filename=fname,
                                   url=fc.final_url, title=title or host,
                                   domain=host, priority=policy.priority_for(host),
                                   quality_tier=tier, topic_ids=[topic_id],
                                   storage_dir=storage_dir, preextracted_text=text)
        except ingest.IngestError as e:
            row["error"] = f"ingest: {e}"
            outcome.errors.append(f"{url}: {e}")
            outcome.fetched.append(row)
            continue
        row.update(res)
        row["ingested"] = True
        outcome.fetched.append(row)
    return outcome


_TIER_RANK_LOCAL = {"authoritative": 0, "educational": 1, "general": 2}


def ingest_bytes_web(db: Session, *, data: bytes, filename: str, url: str,
                     title: str, domain: str, priority: int, quality_tier: str,
                     topic_ids: list[int], storage_dir: str | None = None,
                     preextracted_text: str | None = None) -> dict:
    """Web variant of ingest_bytes that records full source metadata (§6):
    URL, title, publisher/domain, retrieval date, hash, priority and tier.

    `preextracted_text` skips re-extraction for already-sanitized HTML text,
    keeping extraction responsibility in one place otherwise (PDFs)."""
    res = ingest.ingest_bytes(db, data=data, filename=filename, title=title,
                              origin="web", url=url, topic_ids=topic_ids,
                              storage_dir=storage_dir,
                              license_note=_license_note(domain),
                              preextracted_text=preextracted_text)
    src = db.get(AcademicSource, res["source_id"])
    if src is not None:                            # record/refresh web metadata (§6)
        src.domain = domain
        src.publisher = src.publisher or domain
        src.priority = priority
        if quality_tier == "unrated" or src.quality_tier in ("unrated", None):
            src.quality_tier = quality_tier
        elif _TIER_RANK_LOCAL[quality_tier] < _TIER_RANK_LOCAL[src.quality_tier]:
            src.quality_tier = quality_tier        # keep best known tier
        db.commit()
    return res


def _license_note(domain: str) -> str:
    if "wikipedia" in domain:
        return "CC BY-SA 4.0 (Wikipedia text); attribution required for reuse"
    if "openstax" in domain:
        return "CC BY 4.0 (OpenStax)"
    if "libretexts" in domain:
        return "CC licensed OER (LibreTexts; verify page footer)"
    if "mit.edu" in domain:
        return "MIT OCW terms — institutional educational use"
    return "unknown — treat as reference evidence only; do not copy verbatim"
