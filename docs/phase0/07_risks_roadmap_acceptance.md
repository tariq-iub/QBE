# AI-QBE — Performance Constraints, Risks, Roadmap & Phase Acceptance Criteria (PHASE 0)

Covers deliverables #17, #18, #19, #20.

---

## 1. Expected Performance Constraints (design budgets — NOT measured results)

All figures are **engineering budgets used for sizing**, to be replaced by Phase-1/Phase-9
measurements before any commitment to the university. Format: budget → what invalidates it.

| Concern | Design budget | Basis / risk |
|---|---|---|
| Generator model resident size | ≤ 5.5 GB VRAM (7–9B @ q4) | GGUF file-size arithmetic; KV cache at 4k ctx must fit remaining ~1.9 GB |
| Context policy | ≤ 4k tokens typical prompt+completion; hard cap 8k | Knowledge-pack slices sized to ≤ 2.5k; batch of 20 MCQs ≈ 2–3k completion tokens |
| Batch generation call | budget 60–180 s per 20-question batch | tokens/sec unknown until Phase 1; if < ~15 tok/s effective, reduce batch or offload ratio |
| Job wall-clock (3,000 approved) | overnight window ≤ 12 h incl. validation passes | implies sustained ≥ ~4–5 candidates/min end-to-end *assumption*; primary Phase-9 acceptance metric |
| Validation LLM cost | ≈ 1.3 generator calls per candidate (verify + occasional judge/difficulty) | dedup L1–L3 deterministic keeps marginal cost low |
| Embeddings | CPU-only acceptable; ingest 100-page PDF ≤ 10 min | mpnet-base throughput on modern x86; GPU-shared optional |
| Qdrant footprint | ≤ 4 GB RAM at 2 M vectors (768-d fp32 HNSW) | int8 quantization halves this [evaluated Phase 3] |
| Postgres | millions of candidates fine with declared indexes/partition-readiness | review-queue partial index is the hot path |
| Disk | models ~15 GB each download; uploads + backups plan 200 GB free | model store outside container images |
| Concurrency | workers = 1 generation slot (GPU serialized) + N validation/CPU workers; queue priorities keep pipeline flowing while GPU busy | over-subscribing LLM server causes context thrash — llama-server parallel slots capped at 2, config-enforced |

**Throughput honesty statement:** we do not claim questions/hour anywhere in Phase 0. The
benchmark harness (Phase 1) and load-test script (`scripts/loadtest_jobs.py`, Phase 9) produce
the numbers; deployment sign-off uses those artifacts only.

## 2. Risks and Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Selected local model too slow for 3,000/question overnight window | Med | High | Adaptive over-gen factor; batch tuning; smaller-model fallback tier (Phi-3.5-mini) for Remember-tier items; multi-night jobs resumable; upgrade path documented |
| R2 | Hallucinated facts survive validators | Med | Critical (academic trust) | Independent verifier + re-retrieval + SymPy override + human gate; quality-score hard-floors; spot-audit sampling report per job [PROD] |
| R3 | Grammar-constrained JSON unsupported/flaky on chosen runtime | Low-Med | Med | Dual-path: schema-validate + repair-prompt retry; Ollama/OpenAI-compat providers as alternates; contract tests pin behavior |
| R4 | Dedup thresholds tuned wrong → either mass false-positive kills or near-dupes flood bank | Med | Med | Configurable bands + borderline LLM judge + labeled paraphrase test set from day one; reviewer "duplicate warning" UI rather than silent drop |
| R5 | Legacy DB schema differs from assumptions | High | Low | Adapter mapping config (never assume names); read-through cache isolates blast radius; contract test against a sample dump |
| R6 | Prompt injection via retrieved web content | Low | High | Fetch guard + sanitizer + data-block framing + citation-closure checks + injection probe tests in benchmark |
| R7 | VRAM contention (LLM + embeddings + OS) destabilizes long jobs | Med | Med | Embeddings pinned to CPU; nvidia-sampler watchdog restarts LLM slot; checkpoint resume tested in CI chaos test |
| R8 | Copyright exposure from ingested textbooks | Med | High | Source governance fields, license attestation CLI gate, verbatim-copy detector, OER-first priority, no bulk scraping default |
| R9 | Reviewer bottleneck (thousands pending) | High | Med | Quality-score triage ordering, bulk approve for high-score homogeneous batches with sampling audit, per-topic assignment queues |
| R10 | Scope creep toward full LMS/exam platform | Med | Med | Hard boundary: generation vs exam selection separated (spec §41); blueprint module isolated |
| R11 | Single workstation = single point of failure | Med | Med | Backups + restore drill; jobs resumable; acceptable for internal tooling SLA (documented, not hidden) |
| R12 | Model update changes outputs silently (reproducibility drift) | Med | Med | Model registry pins hash/version; prompt templates versioned+hashed; regeneration always creates new candidate rows, never mutates old provenance |

## 3. Implementation Roadmap (phases with dependencies)

```
P0 design (this doc set) ──► P1 LLM/embed benchmark on real hardware ─┬─► P2 core DB+API+jobs+auth
                                                                       └─► P3 document RAG+kpacks ─► P4 web retrieval
P2,P3 ─► P5 generation engine ─► P6 scientific notation ─► P7 QA pipeline ─► P8 review UI
P7+P8 ─► P9 scale runs & tuning ─► P10 exam integration ─► P11 production hardening → pilot
Est. sequencing note: P6 can partially overlap P5 (validators independent of planner).
Gate rule: a phase's acceptance criteria (§4) must pass before dependent phase starts coding
its risky parts (e.g., no P7 auto-approve logic before P1 model thresholds known).
```

## 4. Acceptance Criteria per Phase

**P0 (this deliverable):** all six docs reviewed & merged; stack decisions recorded as ADRs;
no code claims. ✔ criteria = stakeholder sign-off checklist in `docs/phase0/SIGNOFF.md`.

**P1:** benchmark harness executes reproducible runs for ≥ 3 quantized candidates + embedding
shortlist on the actual 8 GB machine; `benchmarks/results/*.md` contains measured tokens/s,
VRAM peaks, JSON pass rates, expert-scored quality rubric; chosen model meets configurable
floors (schema ≥ 95 %, verify-agreement ≥ 85 % on task set) or deviation explicitly accepted
by academic lead; mock-provider contract tests green in CI.

**P2:** Alembic migrations create full ERD schema w/ constraints+triggers; legacy adapter reads
a provided MySQL sample dump through mapping YAML; job CRUD + lifecycle endpoints work against
Redis/RQ with checkpoint table; authn/z RBAC enforced in API tests; provenance closure query
returns complete trace for seeded synthetic candidates; unit+integration tests green; no RAG/web
yet (context supplied manually via API payload).

**P3:** PDF/DOCX/MD/TXT ingestion preserves page/section metadata; chunker never splits formula
blocks (property tests); embed→Qdrant→filtered retrieve round-trip recall@5 ≥ target on labeled
topic set (threshold set after first measurement, baseline recorded); knowledge packs generated
with every element carrying chunk refs; rebuild idempotent.

**P4:** fetch-guard blocks non-allowlisted domains, private IPs, redirect escapes (attack-suite
tests pass); sanitizer strips scripts/comments; injection probes quarantined; web sources flow
through same ingest path with origin/policy_version recorded; internet toggle off ⇒ zero egress
(verified by network-policy test).

**P5:** planner allocation math property-tested (weights, distributions sum, batch sizes 10–30,
concept max-share honored); grammar+schema pipeline yields ≥ 95 % parse success on chosen model;
skip-instead-of-invent behavior demonstrated on evidence-starved plans; retries bounded and
metered; option word-limit validator live.

**P6:** LaTeX/mhchem fixtures (math, physics, chemistry suites incl. malformed cases) validated
with machine-readable findings; dimensional/unit checker passes curated physics set; SymPy
equivalence verifier tested on algebra/calculus corpus; review UI renders MathJax correctly
(source stored unchanged); malformed notation cannot reach APPROVED (state-machine test).

**P7:** all validators wired as separate worker stages; verifier catches planted-wrong-answer
fixtures (≥ 90 % on seeded adversarial set — final number from P1 task set); dedup levels 1–3 +
judge band on labeled paraphrase pairs (precision/recall reported, thresholds tuned); quality
score reproduces deterministically from stored results (golden tests); answer-position histogram
within configured tolerance on 1,000-candidate soak.

**P8:** reviewer sees full panel (question/evidence/scores/dup warnings); edit → version row +
targeted revalidation; bulk review with audit entries; filters+persistence; UI usable by a
non-developer academic in scripted walkthrough.

**P9:** soak runs 100→500→1,000→3,000 targets on production hardware; measured throughput,
rejection/duplicate/yield rates published; kill -9 worker mid-job → resume without duplicate
generation (chaos test); GPU/RAM telemetry captured; tuning report adjusts batch/concurrency
config; overnight-window feasibility verdict (pass/fail with data, not aspiration).

**P10:** blueprint constraint solver returns selections satisfying topic/difficulty/Bloom specs
or reports infeasibility; order+option randomization verified statistically; exports (JSON/CSV/
XLSX) round-trip validated against schema; exam-system push adapter contract test.

**P11:** backup+restore drill executed end-to-end on staging copy; monitoring dashboards live;
security review checklist (incl. dependency scan, pen-test-lite of authz matrix & SSRF suite)
closed; runbooks (upgrade, model swap, incident) written; pilot go/no-go with academic board.

---

## 5. MVP vs Production vs Future — consolidated view

* **MVP** = P2–P7 core loop usable headless/API-first with local documents only, minimal RBAC,
  manual-context generation available before RAG completes, tests green. Pilot users: 2–3 subject
  experts via API + simple admin pages.
* **Production** = + P4 web research governance, P8 UI, P9 reliability at scale, P10 blueprints/
  exports, P11 ops hardening, observability dashboard, source-governance enforcement, TOTP,
  tamper-evident audit chain.
* **Future** = CTA/IRT item analytics, adaptive exam assembly, multilingual banks, second GPU /
  multi-worker fleet, non-MCQ item types, LTI/exam-LMS deep integrations, learned difficulty
  calibration from real attempts.
