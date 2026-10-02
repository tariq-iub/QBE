# ADR-001 — LLM runtime: llama.cpp server with Ollama/OpenAI-compat fallbacks
Date: 2026-10-03 · Status: Accepted (Phase 0) · Deciders: Principal architect (pending academic board sign-off)

**Context.** 8 GB VRAM workstation; need quantization control, GPU-layer offloading, bounded
context, and reliable structured output; vendor independence mandated.

**Decision.** Primary runtime = `llama-server` (llama.cpp) exposing OpenAI-compatible API with
GGUF models and GBNF grammar-constrained JSON. Provider abstraction (`ILLMProvider`) makes
Ollama and any OpenAI-compatible local server drop-in alternates. Model selection deferred to
Phase-1 benchmark (shortlist in docs/phase0/04).

**Consequences.** (+) Fine-grained VRAM control; grammar sampling directly supports "never accept
malformed JSON"; easy future swap to vLLM server behind same provider interface. (−) We own model
file management/hashes; grammar support must be regression-tested per llama.cpp build pin.

# ADR-002 — Queue & workers: Redis + RQ instead of Celery/Kafka/RabbitMQ
Status: Accepted. Single-workstation operational simplicity wins; priorities + delayed retries +
SSE-friendly polling suffice; checkpoint table (not queue state) is the durability source, so
broker loss ≠ job loss. Swap boundary isolated in `jobs/queue` client module. Kafka rejected as
over-provisioned; Celery rejected for extra deps/complexity at this scale.

# ADR-003 — PostgreSQL owns relational truth; Qdrant owns vectors; no dual-write ambiguity
Status: Accepted. Chunks' text+metadata live in Postgres; vectors + payload filters in Qdrant
with `qdrant_point_id` link. Reconciliation script rebuilds vector store from Postgres (source of
truth), never the reverse. Backups: pg_dump authoritative; Qdrant snapshot restorable/rebuildable.

# ADR-004 — Vector backend adapter with FAISS fallback
Status: Accepted. `rag.store` protocol implemented by QdrantBackend (default) and FaissBackend
(embedded, SQLite metadata sidecar already in Postgres). Decision gate revisited in Phase 3 only
if ops rejects Qdrant container. Prevents lock-in argument without adding code forks.

# ADR-005 — Generation and validation are separate pipeline stages communicating via DB state
Status: Accepted. Each stage = idempotent worker task advancing `mcq_candidates.status`; results
append-only in `mcq_validation_results`. Enables independent scaling (GPU vs CPU workers),
auditing, replay, and chaos-resume tests. Forbids in-process shortcuts that would couple model
output to acceptance.

# ADR-006 — Knowledge packs mediate all generation; raw retrieval never feeds MCQ prompts directly
Status: Accepted. Packs carry per-element chunk refs; generation prompts receive pack excerpts +
numbered evidence blocks. Misconceptions feed distractors only. Cost: extra LLM pass per topic;
benefit: grounding traceability, diversity slots, injection surface reduction.

# ADR-007 — MathJax LaTeX as canonical notation; mhchem optional per deployment flag
Status: Accepted. Storage = UTF-8 text, inline `\(..\)`, display `$$..$$`; normalizer on ingest;
validator whitelist per profile. Review UI/export templates load mhchem iff enabled. Images for
math prohibited.

# ADR-008 — Human approval is the only path into the approved bank
Status: Accepted. No auto-approve even at quality 100 (MVP–PROD); APPROVED requires reviewer
principal recorded in `mcq_reviews`. Bulk actions allowed but audited per-item. Removes a class
of silent-failure risk; cost is reviewer throughput → mitigated by triage ordering (R9).

# ADR-009 — Legacy academic DB accessed read-only through mapping-configured adapter + cache
Status: Accepted. Zero writes upstream; canonical ids minted locally; external ids retained.
Schema drift contained to mapping YAML; sync freshness surfaced in UI.

# ADR-010 — Local JWT authn, no external IdP in v1
Status: Accepted. Self-host constraint; OIDC/SAML bridge reserved as FUT extension point in
`security/authn` factory. Argon2id hashing; short-lived tokens; API keys hashed at rest.
