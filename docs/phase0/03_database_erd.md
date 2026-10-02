# AI-QBE — Database Design & ERD (PHASE 0)

Two databases: **legacy academic DB** (university-owned, read-only via adapter) and
**ai_qbe PostgreSQL schema** (owned by this system). Full DDL ships with Phase 2 as Alembic
migrations; this document is the authoritative design.

---

## 1. Legacy Adapter Boundary

```
University MySQL/MariaDB                ai_qbe (Postgres)
program / semester / subject / topic ──► academic_cache(programs, semesters, subjects,
   (names unknown to us; column mapping     topics, subtopics) + last_synced_at,
    configured in legacy_source_map YAML)   external_id per row  ← sync job [PROD]
                                             MVP: on-demand read-through cache
```

Rules: never write upstream; all FKs from AI tables point at `academic_cache`, so jobs remain
reproducible even if the legacy schema mutates. External ids are always stored alongside.

---

## 2. ERD (crow's-foot, ASCII)

```
                         ┌───────────────────┐
                         │ generation_models │  model registry (name, quant, hash, params)
                         └─────────┬─────────┘
        ┌──────────────┐           │1
        │ prompt_      │           │
        │ templates    │•──────────┤
        │ (id,kind,ver)│  N        │
        └──────┬───────┘   ┌───────▼────────────┐        ┌──────────────────────┐
               │           │  generation_jobs   │1──────N│ generation_job_topics│
               │           │  status, targets,  │        │ topic_id, weight,    │
               │           │ distributions,     │        │ bloom/diff overrides │
               │           │ model_profile(jsonb)│       └──────────────────────┘
               │           └───┬───────────┬────┘
               │               │1          │1
               │        ┌──────▼─────┐  ┌──▼───────────────┐   ┌──────────────────┐
               │        │job_check-  │  │ generation_plan  │   │ topic_knowledge_ │
               │        │ points     │  │ batch rows:      │N─►│ packs            │
               │        │(stage,batch│  │ topic,concept,   │   │ jsonb sections + │
               │        │ _cursor,   │  │ bloom,diff,type, │   │ chunk refs       │
               │        │ state jsonb)│  │ count,seq       │   └───────┬──────────┘
               │        └────────────┘  └──────────────────┘           │1
               │                                                       │N
┌──────────────▼──────────┐   ┌────────────────┐   ┌───────────────────────┐
│ academic_sources        │1─N│ source_documents│1─N│ document_chunks       │
│ origin(local/web), url, │   │ file_hash,path, │   │ text, page, section,  │
│ domain, priority,       │   │ license_note,   │   │ qdrant_point_id,      │
│ quality_tier, retrieved │   │ extracted_at    │   │ token_count, meta jsonb│
│ _at                     │   └────────────────┘   └───────────────────────┘
└──────────┬──────────────┘
           │1                                     ┌─────────────────┐
           │N (mcq_sources is M:N bridge)        │ mcq_options     │
┌──────────▼──────────────┐                      │ candidate_id FK,│
│ mcq_candidates          │1────────────────────►│ letter, text,   │
│ job_id FK, plan_id FK,  │  4..5                │ is_correct(bool │
│ topic/subtopic, stem,    │                      │ generated, not  │
│ explanation, bloom, diff,│                      │ user-trusted)   │
│ status(enum workflow),   │──┐                  └─────────────────┘
│ quality_score smallint,  │  │1
│ confidence numeric,      │  │N
 ├── provenance: model_id FK│  ├───────────────►┌──────────────────────┐
 │   ,prompt_template_id FK │  │                │mcq_validation_results│
 │   ,params jsonb, seed,   │  │                │ stage(enum), verdict,│
 │   created_at             │  │                │ score, details jsonb │
└──────┬─────────┬──────────┘  │                └──────────────────────┘
       │1        │1            │1
       │N        │N            │N
┌──────▼─────┐ ┌──▼──────────┐ ┌▼───────────────┐   ┌──────────────────┐
│mcq_sources │ │mcq_reviews  │ │ mcq_versions   │   │duplicate_links   │
│candidate_id│ │reviewer_id, │ │ version_no,    │   │winner_id,        │
│source_id,  │ │action(enum),│ │ snapshot jsonb,│   │loser_id, level,  │
│chunk_id,   │ │reason, at   │ │ editor_id      │   │similarity        │
│evidence_txt│ └─────────────┘ │ (triggered on  │   └──────────────────┘
└────────────┘                 │  every edit)   │
                               └────────────────┘
Additional: users, roles, api_keys, audit_logs(actor, action, entity, entity_id,
diff jsonb, ip, at), generation_metrics(job_id, stage, metric_key, value numeric,
window ts), exam_blueprints + exam_blueprint_items + exam_selections [FUT-ready],
item_statistics(attempt/correct counts, difficulty_index, discrimination_index,
option_distribution jsonb) [schema reserved, no writes until FUT].
Approved bank = view/materialized view over mcq_candidates WHERE status='APPROVED'
(+ current version fields), so "bank" needs no separate mutable copy.
```

---

## 3. Table Notes & Key Constraints

| Table | Critical constraints / indexes |
|---|---|
| generation_jobs | CHECK(target_approved > 0 AND ≤ max_per_job config); status enum w/ partial index on non-terminal states for worker polling; `UNIQUE(idempotency_key)` |
| generation_job_topics | PK(job_id, topic_id); weight numeric CHECK(weight>0); bloom_dist & diff_dist jsonb validated app-side against sum-to-1.0 |
| job_checkpoints | PK(job_id, stage); batch_cursor int; state jsonb — **the resume mechanism** (NFR-5) |
| generation_plan | index(job_id, seq); concept_slot varchar nullable → diversity coverage metric joins here |
| academic_sources | origin IN ('local_document','web','institutional'); domain lower(); priority int; quality_tier smallint; web rows require fetched_at + policy_version that admitted them |
| source_documents | UNIQUE(file_sha256) dedupes re-uploads; license_note text NOT NULL for textbook class sources |
| document_chunks | UNIQUE(document_id, chunk_index); qdrant_point_id uuid ↔ vector store consistency; gin(to_tsvector(text)) for lexical dedup L2 & BM25-style retrieval assist |
| topic_knowledge_packs | UNIQUE(topic_id, job_id, build_version); sections stored as jsonb arrays each element carrying `source_chunk_ids` → grounding traceable into the pack itself |
| mcq_candidates | status enum: GENERATED, STRUCTURE_VALIDATED, FACT_VALIDATED, DEDUPLICATED, QUALITY_CHECKED, PENDING_REVIEW, APPROVED, REJECTED, NEEDS_REVISION, DUPLICATE, INVALID, LOW_CONFIDENCE; **CHECK(quality_score BETWEEN 0 AND 100)**; stem_norm text generated (lower/strip/punct-fold) + UNIQUE within job during dedup pass (deferred constraint) for L1; provenance columns ALL NOT NULL (model_id, prompt_template_id, params jsonb, seed, created_at) — FR/NFR-6 hard requirement; composite index (job_id,status), (topic_id,status), partial idx WHERE status='PENDING_REVIEW' for review UI queue |
| mcq_options | UNIQUE(candidate_id, letter); word_count int generated col (math-aware counting app-side for LaTeX tokens); exactly-one-correct enforced **app-side after validation**, DB CHECK cannot express cross-row uniqueness of truth — trigger `assert_single_correct(candidate)` fires before status promotion past STRUCTURE_VALIDATED |
| mcq_validation_results | UNIQUE(candidate_id, stage, attempt); verdict PASS/FAIL/UNCERTAIN; drives quality score inputs; kept immutable (append-only attempts) |
| mcq_sources | evidence_txt snippet + char offsets into chunk → reviewer sees exact grounding; FK(chunk_id) RESTRICT delete |
| mcq_versions | BEFORE UPDATE trigger on mcq_candidates content columns ⇒ insert snapshot; reviewers edit safely, history auditable |
| duplicate_links | level ∈ {exact,lexical,embedding,judged}; similarity numeric(4,3); loser keeps status DUPLICATE with link → never silently deleted (provenance rule) |
| audit_logs | append-only (REVOKE UPDATE/DELETE); partition by month when >50M rows [PROD ops note] |
| item_statistics | one row per question (FK APPROVED only via deferrable trigger); all counters default 0 — reserved for CTA/IRT [FUT] |

Scale notes (millions of questions): bigint PKs throughout (`BIGSERIAL`/identity),
`mcq_candidates`+`document_chunks` partition-ready by `created_at` range (declared now,
enabled at first partition event), TOAST-friendly text storage, covering indexes for the
three hottest queries (review queue, bank export, dedup scan). Vector heavy-lifting stays in
Qdrant; Postgres stores metadata mirror only (single source of truth per concern documented
in ADR-004).

---

## 4. Provenance Trace Example (acceptance test target for Phase 2)

Given any approved MCQ id, a single recursive query must return: job → subject/topic →
plan batch → model(+quant+hash) → prompt template(+version) → params+seed → knowledge pack →
chunks → documents → sources(url/file, retrieved_at, license) → validation results →
reviewer actions → version chain. This closure is the FR "never lose provenance" test.
