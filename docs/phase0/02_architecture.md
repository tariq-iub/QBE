# AI-QBE — System Architecture & End-to-End Data Flow (PHASE 0)

Documents: #2 Technology Stack, #3 Architecture Diagram, #4 ERD pointer, #9 RAG,
#10 Generation, #11 Validation, #16 Deployment.

---

## 1. Recommended Technology Stack

| Layer | Choice | Rationale / Alternative considered |
|---|---|---|
| Language | **Python 3.12**, fully typed | Ecosystem for LLM/RAG/validation; mypy strict in CI |
| API framework | **FastAPI + Pydantic v2** | Async, schema-first (same Pydantic models reused as LLM output contracts), OpenAPI auto-docs |
| Relational DB | **PostgreSQL 16** | Own schema for all AI artifacts; JSONB for flexible validation payloads; generated columns + partial indexes for workflow states |
| Legacy academic DB | **MySQL/MariaDB via read-only SQLAlchemy adapter** | University-owned; never written to; column mapping config decouples us from their schema |
| Migrations | **Alembic** | Reproducible schema evolution |
| Queue / workers | **Redis 7 + RQ (redis-queue)** with job-checkpoint pattern | Chosen over Celery/Kafka: single-workstation simplicity, one extra container, priorities support, trivially observable. Kafka is unnecessary at this scale (**FUT**: swap boundary is the queue interface) |
| Vector store | **Qdrant** (embedded-mode fallback: FAISS index files) | Metadata-filtered ANN search natively (subject/topic/source filters required by spec); server mode cheap on RAM; FAISS justified only if Qdrant ops prove too heavy — decision gate in Phase 3 |
| Embeddings | **sentence-transformers** running `paraphrase-multilingual-mpnet-base-v2` initially; evaluation candidates: `bge-m3`, `nomic-embed-text-v1.5` (see doc 06) | CPU-friendly (~500 MB model), strong multilingual, 768-dim keeps index small. Final pick after Phase 1/3 eval — not claimed yet |
| LLM runtime | **llama.cpp (`llama-server`, OpenAI-compatible `/v1/completions|chat`)** primary; **Ollama** supported | GGUF quantization control (n-gpu-layers offload), grammar-constrained JSON via GBNF grammars — decisive for "never accept malformed JSON" |
| Structured output | llama.cpp grammar sampling + JSON Schema validation (double safety) | Constrained decoding alone is not trusted; schema validation still gates every response |
| Math verification | **SymPy** | Deterministic algebra/calculus checking |
| LaTeX validation | Custom parser module + `pylatexenc` token checks | Balanced braces/commands whitelist; mhchem handled by dedicated rules |
| PDF/text extraction | `pypdf` + `pdfplumber`, `markdown-it-py`, DOCX via `python-docx` | Page/section metadata preservation |
| Web research | `httpx` client behind a **fetch-guard proxy module** (allowlist enforced server-side, SSRF guards) | Search backend pluggable: SearXNG instance (self-hosted) preferred; any REST search API adapter-compatible |
| Frontend | **React + TypeScript + Vite**, MathJax 3 | Review UI needs MathJax rendering; keep thin — logic stays in API |
| AuthN/Z | Local **JWT** (short-lived access tokens) + hashed passwords (`argon2-cffi`), RBAC claims; API keys for service integrations | No external IdP dependency for self-hosting; OIDC bridge listed as FUT |
| Observability | `structlog` JSON logs + **Prometheus** `/metrics` + Grafana compose profile | MVP ships structured logs + metrics counters; dashboard PROD |
| Packaging | Docker Compose (profiles) + native systemd guide | One workstation = one compose file |
| Testing | pytest, hypothesis, testcontainers (Postgres/Redis/Qdrant), respx/httpx mock for providers | Contract tests per layer |

**Deliberate exclusions:** no Kubernetes, no microservices split beyond api/worker/llm/qdrant/
postgres/redis containers, no LangChain-style orchestration framework (pipeline stages are
explicit code with typed interfaces — auditability requirement FR-2/NFR-6).

---

## 2. System Architecture Diagram

```
                         ┌────────────────────────────────────────────────────────┐
                         │                    ADMIN / REVIEWER BROWSER            │
                         │              React SPA (MathJax rendering, RBAC)       │
                         └───────────────▲────────────────────────────────────────┘
                                         │ HTTPS (LAN only)
┌──────────────────────┐        ┌────────┴─────────────────────────────────────┐
│ EXISTING ACADEMIC DB │        │                API SERVICE (FastAPI)         │
│ MySQL/MariaDB        │◄──read─┤  authn/z · validation · job CRUD · review    │
│ (university-owned)   │  only  │  questions · exports · metrics endpoint      │
└──────────────────────┘        │  (NEVER calls LLM synchronously for jobs)    │
                               └───┬───────────────▲───────────────┬──────────┘
             enqueue stage tasks   │               │ status/metrics│ queries
                               ┌───▼───────────────┴───┐           │
                               │      REDIS (queue)     │           │
                               │ queues: prep, retrieve,│           │
                               │ generate, validate,    │           │
                               │ dedup, score           │           │
                               └───┬────────────────────┘           │
        ┌──────────────────────────┼───────────────────────────┐    │
        ▼                          ▼                           ▼    │
┌───────────────┐        ┌──────────────────┐        ┌────────────────────┐
│ WORKER POOL   │        │ POSTGRES 16      │◄───────┤  (all state lives  │
│ (Python RQ)   │        │ ai_qbe schema:   │        │   here: jobs,      │
│ prep/retrieve │        │ jobs, packs,     │        │   candidates,      │
│ generate      │        │ chunks-meta, mcq │        │   reviews, audit)  │
│ validate/dedup│        │ *_validation...  │        └────────────────────┘
└───┬───────┬───┘        └────────▲─────────┘
    │       │                     │ embeddings+metadata
    │       │              ┌──────┴───────┐        ┌──────────────────────┐
    │       └─────────────►│ QDRANT       │◄───────┤ EMBEDDINGS MODEL     │
    │          query/insert│ (vector+payload│      │ sentence-transformers│
    │                      └──────────────┘        │ (CPU or GPU-shared)  │
    │ prompts+context                                └────────────────────┘
    ▼
┌───────────────────────────┐     ┌────────────────────────────────────────┐
│ LOCAL LLM SERVER(S)       │     │ RETRIEVAL PROXY (fetch-guard)          │
│ llama-server (GGUF q4_K_M)│     │ allowlist/blocklist · SSRF guard ·     │
│ - generation model        │     │ HTML/script sanitize · license notes   │
│ - verifier model (may be  │     └───────────────▲────────────────────────┘
│   same weights, separate  │                     │ approved domains only
│   context slot)           │        ┌────────────┴───────────┐   Ollama also
│ - judge model (dup/bloom) │        │ Internet: SearXNG /    │   supported as
│ OLLAMA COMPATIBLE SWAP    │        │ curated academic sites │   provider
└───────────────────────────┘        │ + local PDFs/notes     │
                                     └────────────────────────┘
```

Key structural guarantees:

1. **Generation and validation are separate worker stages** communicating only through
   Postgres state transitions — never an in-process shortcut.
2. The LLM endpoint binds to localhost/docker-internal network only (NFR-3).
3. Every pipeline stage writes provenance rows atomically with its result.
4. API service contains zero long-running work; it enqueues and returns.

---

## 3. End-to-End Data Flow (numbered, auditable)

```
 1. Admin picks Program→Semester→Subject→Topics (from legacy adapter cache)
 2. POST /api/v1/jobs → row: generation_jobs(status=queued) + generation_job_topics(weights,
    bloom/difficulty distributions, source_policy, model_profile_id, prompt_profile)
 3. Worker [PREP] : for each topic → Retrieval Stage:
      3a. Local docs: source_documents → extract → clean → chunk (document_chunks, metadata)
      3b. Embeddings → Qdrant points {subject_id, topic_id, source_id, chunk_id, page, section}
      3c. (PROD) Internet: fetch-guard → sanitized text → same ingest path (source marked
          origin=web, domain policy applied at fetch time)
      3d. Knowledge-pack build: LLM(knowledge_extraction_vN, grounded ONLY in retrieved
          chunks) → TopicKnowledgePack row (concepts, definitions, formulas, laws, facts,
          misconceptions, chunk refs, source refs). Status: knowledge_prepared
 4. Worker [PLAN] : MCQ Planning Engine → generation_plan rows:
      target_approved × over_gen_factor → candidate budget → allocate across
      topics×subtopics×concepts×Bloom×difficulty×qtype respecting weights → batches of 10–30
 5. Worker [GENERATE] (batch b): prompt = mcq_generation_vN + knowledge pack slice +
      evidence blocks (delimited as untrusted DATA) + format constraints (option word limits,
      LaTeX/MathJax rules, single-correct rule) → LLM w/ grammar-constrained JSON →
      parse → schema-validate → rows: mcq_candidates(status=GENERATED) + mcq_options +
      mcq_sources + provenance(model_id, model_version, prompt_version, params, seed, ts)
      Malformed/unparseable → bounded retry then INVALID (counted in metrics)
 6. Worker [VALIDATE-STRUCTURE] : deterministic checks → STRUCTURE_VALIDATED | INVALID
 7. Worker [VALIDATE-ANSWER] : independent verifier pass (answer_verification_vN, re-retrieved
      evidence; SymPy for computable items) → FACT_VALIDATED | REJECTED | LOW_CONFIDENCE
      (UNCERTAIN → forced human review flag)
 8. Worker [VALIDATE-DISTRACTOR + NOTATION] : distractor rules + LaTeX/chem/unit validators
      → results rows; failures → NEEDS_REVISION/REJECTED
 9. Worker [DEDUP] : L1 sha256(normalized stem) → L2 lexical TF-IDF/ratio → L3 embedding kNN
      (topic-scoped + global) → borderline band → duplicate_judge_vN LLM → DUPLICATE |
      DEDUPLICATED ; thresholds from config
10. Worker [CLASSIFY] : difficulty_classification_vN + heuristic features → difficulty/Bloom
     stored; plan-match check feeds quality score
11. Worker [SCORE] : composite quality_score(0–100) from measurable results → QUALITY_CHECKED
     → auto-transition PENDING_REVIEW (if score ≥ auto_review_floor) else REJECTED_LOW_QUALITY
12. Option-position balancing runs at PENDING_REVIEW entry: shuffle preserving correct map,
    record position histogram per job
13. Human Review UI/API: approve → APPROVED (bank); reject → REJECTED; edit → mcq_versions
    new row + re-run affected validators; regenerate → new candidate linked to parent
14. Export/Blueprint selectors read APPROVED only; exam selection is a separate module
15. Metrics at every stage → generation_metrics (time-series) + Prometheus exposition
```

Failure at any step: task retried with backoff; job checkpoint table records last completed
batch per stage so resume (step 2 of NFR-5) never re-generates accepted batches.

---

## 4. Pipeline Modularity

Each numbered flow block maps to a package with a narrow interface:

```
retrieval.ingest | retrieval.search | rag.embed | rag.store | knowledge.pack_builder
generation.planner | generation.batch | llm.provider(ILLMProvider) | llm.prompts | llm.schemas
validation.structure | validation.answer | validation.math | validation.distractor
validation.notation | dedup.levels | classify.difficulty_bloom | scoring.quality
workflow.state_machine | review.service | export.service | jobs.queue | jobs.checkpoints
```

Interfaces are Pydantic models + Protocols; swapping Qdrant↔FAISS, Redis↔(FUT broker),
llama.cpp↔Ollama touches one adapter each.

---

## 5. Deployment Architecture

### 5.1 Reference topology (single 8 GB-GPU workstation) — Docker Compose profiles

```
profile core:    postgres, redis, api, worker, llm-gen (llama-server)
profile rag:     qdrant, embedder (sidecar or in-worker process)
profile verify:  llm-verifier (second llama-server slot OR reuse llm-gen with separate
                 context; decided by Phase-1 VRAM measurements — default: reuse, since two
                 resident 4-bit ~7–8B models will not fit in 8 GB simultaneously)
profile web:     retrieval-proxy (fetch-guard), searxng (optional)
profile ui:      frontend (nginx serving SPA, LAN only)
profile obs:     prometheus, grafana
```

Resource plan (to be confirmed by Phase-1 benchmark, not asserted): generator GGUF
q4_K_M with `-ngl` tuned to fill ~5.5–6 GB VRAM leaving headroom for KV-cache capped at a
bounded context (≤ 8k, typically 4k); embeddings run on CPU (fits comfortably in 32 GB RAM);
Postgres/Redis/Qdrant < 2 GB combined.

### 5.2 Native alternative
systemd units: `ai-qbe-api`, `ai-qbe-worker@1..N`, `llama-server`, plus distro packages for
postgres/redis/qdrant binary. Documented in `deployment/native-install.md` (Phase 11 deliverable).

### 5.3 Growth path (no rewrite)
Bigger GPU → raise `-ngl`/context, add verifier slot. Multi-node → move queue to Redis
replication/TLS, workers onto CPU nodes (generation is the only GPU-bound stage), Postgres
to managed instance. All boundaries already exist as network protocols (HTTP/RESP/TCP).

### 5.4 Network posture
```
[Internet] ⇢ allowed OUTBOUND only via retrieval-proxy (domain policy)
[LAN]      ⇢ frontend + API (TLS, auth required)
[internal] ⇢ llm-*, qdrant, postgres, redis — bound to docker network only, no host publish
```
