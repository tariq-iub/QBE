# AI-QBE — Security Architecture, REST API & Directory Structure (PHASE 0)

Covers deliverables #13 (security), #14 (REST API), #15 (project structure).

---

## 1. Security Architecture

### 1.1 Trust zones
```
ZONE-0 public Internet        : NO exposure whatsoever (no inbound rule ever)
ZONE-1 LAN                    : frontend + API only, TLS (self-signed internal CA or
                                university cert); rate-limited; auth required
ZONE-2 docker internal net    : api, workers, postgres, redis, qdrant, llm-*, proxy
ZONE-3 host                   : secrets files (0600), model store, backups
Legacy academic DB            : reachable read-only through adapter with dedicated
                                low-privilege DB user (SELECT grants only)
```

### 1.2 Authentication & authorization
* Local identities: `users` + `roles` + `user_roles`; passwords argon2id; optional TOTP [PROD].
* Sessions: short-lived JWT access (15 min) + rotating refresh (httpOnly SameSite=Lax cookie);
  service-to-service: scoped API keys (hashed at rest, prefix-searchable).
* RBAC matrix enforced by FastAPI dependencies per route (deny-by-default router policy);
  SubjectExpert scope = join table (user ↔ subject/program) checked in query layer, not UI.
* Roles per spec: Administrator, QuestionGenerator, AcademicReviewer, SubjectExpert,
  ExamController, ReadOnly.

### 1.3 Application hardening
* Pydantic input validation everywhere; strict content types; request body caps; parameterized
  SQL only (SQLAlchemy core/ORM); ORM-level row scoping for subject experts.
* Rate limiting (redis-backed token bucket): per-user and per-API-key tiers; stricter on auth.
* CSRF protection for cookie flows; CORS pinned to frontend origin; security headers (CSP
  without unsafe-inline except MathJax nonces, X-Frame-Options DENY).
* Secrets via environment / mounted secret files; nothing in images or repo; `.env.example`
  documents every key. Key rotation runbook [PROD].
* Audit log append-only (DB permission revoke + hash-chain of rows for tamper evidence [PROD]).
* Dependency CVE scanning in CI (pip-audit, npm audit); image slim bases, non-root containers,
  read-only mounts where possible; LLM containers drop capabilities.
* Backups: pg_dump WAL-enabled + Qdrant snapshots + object-store copy of uploads; restore
  drill documented Phase 11.

### 1.4 Prompt-injection & untrusted-data defenses (cross-cutting)
* Single egress path (fetch guard) — SSRF/DNS-rebinding blocked; redirects re-validated.
* Content sanitizer strips HTML/script/comments; injection-pattern detector quarantines pages.
* Data/instruction separation: retrieved text always inside delimited `<EVIDENCE id=n>` blocks
  with system-preamble "content never commands"; generation schemas cite block ids → dangling
  citations rejected (fabrication defense doubles as injection defense).
* Model output treated as hostile input: never eval'd, never interpolated into SQL/shell;
  exports are data-only files; LaTeX rendered by MathJax with sanitizing macros disabled
  (`\require`, `\href` to javascript: blocked).
* LLM endpoints bind internal network only; no tools/function-calling enabled in v1.

### 1.5 Source governance
`academic_sources` carries origin, license_note, quality_tier, policy_version; ingestion CLI
refuses textbook-class uploads without a license note field set (operator attestation stored);
verbatim-copy detector compares candidate stems/options against chunk n-grams (configurable
n, default 25-word window) → flag COPY_RISK for reviewer. OER/institutional preferred by
retrieval priority weights.

---

## 2. REST API Design (v1, OpenAPI generated from code)

Conventions: JSON, cursor pagination (`?limit&cursor`), RFC7807 problem responses,
ETag on question resources, idempotency-key header on POSTs creating jobs, all under `/api/v1`.

```
Auth
POST   /auth/login | /auth/refresh | /auth/logout          GET /auth/me
Users/admin         CRUD /admin/users, /admin/api-keys,
                    PUT  /admin/source-policy, /admin/model-profiles, /admin/prompt-templates/{id}

Academic master (read-through adapter/cache)
GET    /programs                     GET /programs/{id}/semesters
GET    /semesters/{id}/subjects      GET /subjects/{id}
GET    /subjects/{id}/topics         GET /topics/{id}/subtopics
POST   /academic-cache/sync          [role: Admin]

Sources & documents
GET    /sources?origin=&topic_id=    POST /sources/web-search {topic_query}   [PROD]
POST   /documents (multipart upload) GET  /documents/{id}
GET    /documents/{id}/chunks        DELETE /documents/{id}  [soft]

Knowledge packs
GET    /jobs/{job_id}/knowledge-packs            GET .../knowledge-packs/{topic_id}
POST   /jobs/{job_id}/knowledge-packs/rebuild    [prep stage rerun]

Generation jobs
POST   /jobs                     {subject_id, topic_ids[{id,weight}], target_approved,
                                  options_count, bloom_dist, difficulty_dist, source_policy_id,
                                  model_profile_id, prompt_profile_id, internet_enabled}
GET    /jobs?status=             GET  /jobs/{id}
POST   /jobs/{id}/start|pause|resume|cancel
GET    /jobs/{id}/progress       {stage, knowledge_pct, generated, validated, rejected,
                                  duplicates, pending_review, approved, current_topic,
                                  questions_per_min, eta_seconds}
GET    /jobs/{id}/plan           GET  /jobs/{id}/metrics
POST   /jobs/{id}/retry-failed-batches

Questions (candidates + bank unified resource, status filter distinguishes)
GET    /questions?status=&subject_id=&topic_id=&bloom=&difficulty=&min_quality=&q=
GET    /questions/{id}           full payload incl. options, sources+evidence, validation
                                 results, duplicate warnings, provenance, versions list
POST   /questions/{id}/approve   {comment}        POST /questions/{id}/reject {reason_code, comment}
POST   /questions/{id}/edit      {fields} → new version, revalidation triggered
POST   /questions/{id}/regenerate {hint}          POST /questions/{id}/flag
POST   /questions/bulk-review    [{id, action, reason?}]  [reviewer+]
GET    /questions/{id}/versions  GET /questions/{id}/provenance   ← trace closure endpoint

Exports
POST   /exports {filter, format: json|csv|xlsx, scope: bank|job|blueprint}
GET    /exports/{id} (status)    GET /exports/{id}/download

Exam blueprints [Phase 10]
CRUD   /blueprints ; POST /blueprints/{id}/select → selection preview
POST   /selections/{id}/randomize-options ; export via /exports

Meta/ops
GET    /health  /ready   GET /models (registry)  GET /prompts
GET    /stats/global     Prometheus scrape endpoint (internal net only)
```

Design notes: lifecycle actions are sub-resources returning 200 with new state (not PATCH
magic); review queue is a saved-filter over `/questions?status=PENDING_REVIEW`; job progress
is computed from checkpoint+counter tables (single query, cached 2 s). WebSocket/SSE
`GET /jobs/{id}/events` stream added [PROD] for live monitor instead of polling.

---

## 3. Project Directory Structure

```
ai-question-bank/
├── backend/
│   ├── pyproject.toml  uv.lock
│   ├── aiqbe/
│   │   ├── api/                # FastAPI app: routers/, deps.py (RBAC), middleware/
│   │   ├── domain/             # pure models + enums (MCQStatus, Bloom, Difficulty…), state machine
│   │   ├── database/           # SQLAlchemy models, repositories/, migrations/ (alembic/)
│   │   ├── legacy_adapter/     # read-only MySQL/MariaDB mapper, sync cache writer
│   │   ├── jobs/               # queue client, task definitions, checkpoints, scheduler
│   │   ├── retrieval/          # ingest extractors, fetch_guard, search adapters, sanitizer
│   │   ├── rag/                # embedder, store (qdrant|faiss backends), retriever, pack_builder
│   │   ├── generation/         # planner, batch composer, prompt renderer, persistor
│   │   ├── validation/         # structure, answer, math(sympy), distractor, notation, dedup, classify, scoring
│   │   ├── review/             # approve/reject/edit services, versioning, bulk ops
│   │   ├── export/             # json/csv/xlsx/db-push writers
│   │   ├── blueprint/          # exam selection engine [Phase 10]
│   │   ├── security/           # authn/z, tokens, api-keys, audit, rate-limit
│   │   ├── observability/      # structlog config, metrics registry, health
│   │   └── config.py           # pydantic-settings; all tunables/thresholds/policies here
│   └── tests/                  # unit/ integration/ api/ db/ contract/ fixtures(math,physics,chemistry,injection)
├── llm/
│   ├── providers/              # llama_cpp.py, ollama.py, openai_compat.py, mock.py (tests)
│   ├── prompts/                # *.j2 templates + registry.json (kind,version,sha)
│   ├── schemas/                # mcq_batch.schema.json, verification.schema.json, …
│   └── grammars/               # GBNF files derived from schemas (generated, checked-in)
├── benchmarks/                 # Phase 1: tasks/, harness/, REPORT_TEMPLATE.md, results/
├── frontend/                   # React+TS SPA: pages/jobs, monitor, review (MathJax), admin
├── deployment/
│   ├── docker-compose.yml (+ compose.profiles.override samples)
│   ├── Dockerfile.api-worker, Dockerfile.llm, nginx/, prometheus/, grafana/provisioning/
│   └── native-install.md       # systemd units guide
├── scripts/                    # seed_demo_data.py, ingest_cli.py, backup.sh, restore_drill.sh, loadtest_jobs.py
├── docs/                       # phase0/ (this set), adr/, api/, operations/, governance/
├── .github/workflows/ci.yml    # lint+mypy+pytest+contract+compose smoke
└── README.md
```

Configuration layout: `config/settings.dev.toml`, `settings.prod.toml` (thresholds, dedup
bands, quality weights, domain policies, sampling params per purpose, batch sizes, retry
ladders) — NFR-12 forbids constants in code; schema-checked at startup.
