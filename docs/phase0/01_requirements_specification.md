# AI-QBE — Requirements Specification (PHASE 0)

**Project:** AI Academic Question Bank Engine (AI-QBE)
**Document:** Requirements Specification v0.1 (Phase 0 deliverable)
**Status:** Design baseline — no production code claimed
**Date:** 2026-10-03

Classification legend used throughout this document:

* **[MVP]** — Minimum Viable Product. Required before internal pilot use.
* **[PROD]** — Production requirement. Required before university-wide deployment.
* **[FUT]** — Future enhancement. Explicitly deferred; schema/architecture must not block it.

---

## 1. Purpose and Scope

AI-QBE is a **self-hosted, privacy-preserving academic assessment generation pipeline**. It is
deliberately *not* an "LLM question generator": the LLM is one component inside a controlled
multi-stage pipeline of retrieval → planning → structured generation → deterministic +
independent validation → deduplication → quality scoring → human review → approved bank.

**In scope:** MCQ (single-best-answer) generation for undergraduate theory subjects
(Physics, Chemistry, Mathematics, CS and similar), grounded in syllabus topics and retrieved
evidence, at volumes up to 3,000+ validated questions per job.

**Out of scope (v1):** essay/short-answer grading, adaptive testing, exam proctoring, student
attempt capture (schema hooks only), non-MCQ item types beyond True/False-as-MCQ.

---

## 2. Stakeholders and Roles [PROD unless noted]

| Role | Capabilities |
|---|---|
| Administrator | Manage users, models, prompt versions, source allowlists, system config |
| QuestionGenerator | Create/start/pause/resume/cancel generation jobs; view metrics |
| AcademicReviewer | Approve/reject/edit/regenerate candidates; bulk review; version history |
| SubjectExpert | Same as reviewer but scoped to assigned subjects/programs |
| ExamController | Build exam blueprints, select question subsets, export exams |
| ReadOnly | View banks, jobs, analytics dashboards |

**[MVP]** A minimal role set (Admin, Generator, Reviewer) must exist from Phase 2 because
auditability of approve/reject actions is mandatory.

---

## 3. Functional Requirements

### FR-1 Academic master-data integration
* FR-1.1 **[MVP]** Read-only adapter over the existing university DB (MySQL/MariaDB or any
  RDBMS) exposing Program → Semester → Subject → Topic → SubTopic. The adapter layer must
  make **no assumption** about the legacy schema; a mapping configuration binds external
  columns to the canonical model.
* FR-1.2 **[MVP]** AI service owns its own PostgreSQL schema for all generated content; it
  never writes to the academic master database.
* FR-1.3 **[PROD]** Sync/cache of master data with change detection (topics added/renamed).

### FR-2 Generation jobs
* FR-2.1 **[MVP]** A job selects subject, topics (with weights), target approved count,
  options-per-question (4 or 5), difficulty distribution, Bloom distribution, source policy,
  model + generation profile.
* FR-2.2 **[MVP]** Jobs are asynchronous: queued / running / paused / completed / failed /
  cancelled, with resumable checkpoints. HTTP requests never span a whole job.
* FR-2.3 **[MVP]** Progress reporting: knowledge-prep %, generated, validated, rejected,
  duplicates, pending-review, approved, current topic, rate, ETA.
* FR-2.4 **[MVP]** Over-generation control: if N approved questions are requested, generate
  candidates until approved-candidate count ≥ N or max-attempts reached, using an
  over-generation factor derived from observed rejection rates (configurable floor, e.g. ×1.2).
* FR-2.5 **[PROD]** Per-topic allocation via a Question Generation Plan (topics × subtopics ×
  Bloom × difficulty × question-type), optionally weighted by teaching hours / instructor
  importance / blueprint. Batches of 10–30 questions per LLM call — never thousands in one request.

### FR-3 Retrieval & grounding
* FR-3.1 **[MVP]** Ingest instructor-supplied documents (PDF, TXT, MD, DOCX) → clean →
  semantic chunk → embed → vector index, with full metadata (subject_id, topic_id, source_id,
  chunk_id, page, section, hash).
* FR-3.2 **[MVP]** Topic Knowledge Pack assembly per topic before generation: concepts,
  definitions, formulas, relationships/laws, facts, misconceptions, retrieved chunks,
  source references. MCQs are generated from the pack, not raw web text.
* FR-3.3 **[PROD]** Controlled Internet retrieval: search adapter restricted to ApprovedDomain
  list, minus BlockedDomain list, with SourcePriority and MinimumSourceQuality gates. Blogs,
  forums and SEO pages excluded by default policy.
* FR-3.4 **[MVP]** Every factual candidate stores question ↔ answer ↔ evidence ↔ source-id
  linkage. If evidence is insufficient, the question is suppressed (never fabricated sources).
* FR-3.5 **[PROD]** Source governance: record license/redistribution notes where available;
  prefer OER/institution-owned material; detect near-verbatim copying of long source spans.

### FR-4 Generation
* FR-4.1 **[MVP]** Model-independent `ILLMProvider` abstraction (llama.cpp, Ollama,
  OpenAI-compatible local servers are interchangeable implementations).
* FR-4.2 **[MVP]** Strict JSON-schema-constrained output; malformed JSON is rejected and
  retried (bounded retries), never accepted silently.
* FR-4.3 **[MVP]** Versioned prompt templates stored in DB/config (knowledge_extraction_vN,
  mcq_generation_vN, answer_verification_vN, difficulty_classification_vN, duplicate_judge_vN);
  every question records prompt_version + model + params + seed.
* FR-4.4 **[MVP]** Stem + 4 (or 5) options + correct option + explanation + topic/subtopic +
  difficulty + Bloom + sources + confidence.
* FR-4.5 **[MVP]** Option conciseness: default target 1–6 words (preferred 1–4); validator
  flags violations; math expressions exempt from naive truncation.

### FR-5 Validation pipeline (generation and validation are separate stages)
* FR-5.1 Structural validator **[MVP]** — schema, counts, non-empty unique options, exactly
  one designated correct option.
* FR-5.2 Answer validator **[MVP]** — independent second pass (different prompt, ideally
  different sampling path): PASS / FAIL / UNCERTAIN with confidence, reason, evidence.
  Only PASS proceeds automatically; UNCERTAIN routes to human review.
* FR-5.3 Deterministic math verifier **[MVP]** — SymPy evaluation for algebraic/calculus
  items where feasible; computational truth overrides LLM reasoning.
* FR-5.4 Distractor validator **[MVP/PROD mix]** — reject irrelevant/duplicate/semantically
  equivalent options, partially-correct alternatives, multiple defensible answers,
  grammatical cues, abnormal length ratios.
* FR-5.5 Scientific-notation validator **[MVP]** — LaTeX well-formedness (balanced braces/
  brackets, known commands), chemistry notation (subscripts/superscripts/charges, optional
  mhchem), unit sanity for physics.
* FR-5.6 Deduplication **[MVP levels 1–3, PROD level 4]** — exact normalized hash → lexical
  similarity → embedding similarity → LLM semantic-duplicate judge for borderline band;
  thresholds configurable; paraphrases of the same fact treated as duplicates for diversity.
* FR-5.7 Difficulty/Bloom classifier **[MVP heuristic, PROD model-assisted]** — features:
  cognitive operation, reasoning steps, distractor similarity, conceptual depth, calculation
  complexity — never sentence length alone.
* FR-5.8 Quality score **[MVP]** — composite 0–100 from measurable validation results
  (factual correctness, grounding, clarity, distractor quality, single-correctness,
  relevance, difficulty match, Bloom match, conciseness, notation validity, duplicate risk).
  Self-reported LLM confidence is never the sole metric.
* FR-5.9 Answer-position balancing **[MVP]** — post-validation shuffle of options preserving
  correct-answer mapping; monitor A/B/C/D distribution across each job.

### FR-6 Diversity control [PROD]
Concept coverage metrics per topic; alert when a small concept subset absorbs the plan
(e.g., 500 questions on 3 of 20 concepts). Planner assigns concept slots explicitly.

### FR-7 Human-in-the-loop review
* FR-7.1 **[MVP]** Review API: view question + options + answer + explanation + provenance +
  evidence + scores + duplicate warnings; actions approve / reject / edit / regenerate / flag /
  change difficulty / change Bloom.
* FR-7.2 **[MVP]** All edits versioned (`mcq_versions`), full audit history retained.
* FR-7.3 **[PROD]** Review UI (web) with MathJax rendering, filters, bulk review.

### FR-8 Status workflow
GENERATED → STRUCTURE_VALIDATED → FACT_VALIDATED → DEDUPLICATED → QUALITY_CHECKED →
PENDING_REVIEW → APPROVED, with terminal states REJECTED, DUPLICATE, INVALID, LOW_CONFIDENCE
and intermediate NEEDS_REVISION. Transitions audited. **[MVP]**

### FR-9 REST API [MVP core, PROD hardening]
Subjects/topics read APIs; job CRUD + lifecycle (start/pause/resume/cancel); question query;
review actions; exports. Professional resource design (see doc 14).

### FR-10 Export & exam selection
* FR-10.1 **[PROD]** Export approved banks to JSON / CSV / Excel / DB push / exam-system API.
* FR-10.2 **[FUT→PROD]** Exam blueprint selection separated from generation: topic/difficulty/
  Bloom constraints, no duplicate concepts, randomized question and option order.

### FR-11 Analytics hooks [FUT]
Schema reserves attempt_count, correct/incorrect counts, difficulty index, discrimination
index, option-selection distribution; CTA then IRT later. No computation implemented in MVP.

### FR-12 Observability [PROD]
Metrics: latency, tokens, questions/min, GPU/RAM utilization, validation failure rate,
duplicate rate, model errors, JSON parse errors, retrieval failures. Operational dashboard.

---

## 4. Non-Functional Requirements

| ID | Requirement | Class |
|---|---|---|
| NFR-1 | Runs on a single workstation: 8 GB VRAM GPU, 32 GB RAM; 4-bit quantized GGUF models, GPU-layer offload configurable, CPU hybrid fallback | MVP |
| NFR-2 | Vendor independence: swapping LLM provider requires config change only | MVP |
| NFR-3 | Privacy: all inference self-hosted; outbound network only through allowlisted retrieval proxy; LLM endpoint never publicly exposed | MVP |
| NFR-4 | Throughput budget: sized so a 3,000-approved-question job completes within an overnight window (~12 h) on the reference hardware — **to be validated empirically in Phases 1 and 9; no numbers claimed here** | PROD |
| NFR-5 | Reliability: jobs survive app restart, worker crash, LLM crash, network/GPU failure; resume from checkpoint without re-generating accepted batches | PROD |
| NFR-6 | Reproducibility/audit: model hash/version, quantization, params, prompt version, seeds, source IDs, software version recorded per question; auditable months later | MVP |
| NFR-7 | Scale: schema designed for millions of questions (partitioning-ready keys, proper indexes) | PROD |
| NFR-8 | Security: authn/z (RBAC), API tokens, rate limiting, input validation, audit logs, secret management via env/secret files, prompt-injection defenses for untrusted retrieved text | PROD (authn core in MVP) |
| NFR-9 | Maintainability: modular repo, typed Python, tests at unit/integration/API/db/schema/RAG/dedup/LaTeX/chem/queue levels | MVP |
| NFR-10 | Operability: Docker Compose for multi-service; documented native alternative; minimal moving parts for a single workstation | PROD |
| NFR-11 | Accessibility of notation: MathJax-compatible LaTeX stored as text; never images | MVP |
| NFR-12 | Config-driven policy: thresholds, distributions, domains, batch sizes, retry limits — no magic constants in code | MVP |

---

## 5. Prompt-Injection & Untrusted-Content Requirements [PROD, stubbed MVP]

* Retrieved web/document text is wrapped as DATA with delimiters; system prompts instruct the
  model to ignore instructions inside data blocks.
* HTML/script stripped during extraction; hidden-instruction heuristics (imperative phrases in
  third-person contexts) flagged.
* Allowlist enforcement happens at fetch time (server-side), not trust-by-URL-string.
* Generation output never executed; exports are data-only.

---

## 6. Testing Requirements (acceptance-linked)

Unit tests for: validators (structure, distractor, notation), planner allocation, dedup
levels 1–3, status machine transitions, option-shuffle correctness-mapping. Integration:
job lifecycle against real queue + Postgres; RAG ingest→retrieve round-trip; provider
contract tests against a mock LLM server. Specific fixtures for Mathematics (LaTeX/SymPy),
Physics (units), Chemistry (formulas/mhchem). Schema tests: every prompt template's expected
JSON validates against its schema; adversarial samples (malformed JSON, multiple correct
answers, paragraph-length options, injection payloads) must fail closed.

---

## 7. Assumptions & Constraints

1. Existing academic DB is read-only to this system; credentials supplied by university IT.
2. One primary inference workstation initially; growth path = bigger GPU/server, same stack.
3. Single-best-answer MCQs only in v1.
4. Internet access, if enabled, is opt-in per deployment and per job.
5. Legal/governance approval for any textbook ingestion is the university's responsibility;
   the system records license metadata and refuses unknown-provenance bulk scraping by default.
6. **No performance/accuracy figures in this document are measured yet.** Phase 1 produces
   the first empirical benchmark report; Phase 9 produces throughput/validation-yield data.

---

## 8. MVP Cut Line (summary)

**MVP (Phases 2–7 usable):** Postgres schema + adapters, jobs with async workers, local-document
RAG + knowledge packs, planner + batched schema-constrained generation, structural/answer/math/
notation validators, 3-level dedup, quality score, review API with versioning, balanced option
positions, basic RBAC auth, tests.

**PROD adds:** internet research w/ domain policy, semantic dup judge (level 4), diversity
metrics, review UI, exports, exam blueprints, observability dashboard, backup/recovery drills,
security hardening.

**FUTURE:** CTA/IRT analytics, non-MCQ item types, multilingual banks, multi-GPU scaling,
student-performance-driven difficulty recalibration.
