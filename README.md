# AI-QBE — Academic MCQ Generation, Validation & Question-Bank Platform

**Self-hosted · privacy-preserving · model-independent · 8 GB-GPU-optimized**

AI-QBE is an *Academic Assessment Generation Pipeline*, not an "LLM question generator".
Reliability comes from: controlled retrieval → Topic Knowledge Packs → generation planning →
schema-constrained structured output → independent + deterministic validation → semantic
deduplication → quality scoring → human academic review → approved, fully-traceable bank.

## Status

| Phase | Description | State |
|---|---|---|
| **0** | Requirements, architecture, ERD, model strategy, security, API design, roadmap | ✅ **Delivered** — see [`docs/phase0/`](docs/phase0/) |
| 1 | Local LLM/embedding benchmark on target hardware (100-task suite) | Planned |
| 2 | Core database + REST API + jobs + auth (no RAG yet) | Planned |
| 3 | Document RAG + Topic Knowledge Packs | Planned |
| 4 | Controlled Internet research (domain policy, fetch guard) | Planned |
| 5 | MCQ generation engine (planner, batches, JSON schemas) | Planned |
| 6 | MathJax LaTeX / physics units / chemistry notation support | Planned |
| 7 | Quality-assurance pipeline (validators, dedup, scoring) | Planned |
| 8 | Human-in-the-loop review UI | Planned |
| 9 | Large-scale soak tests (100 → 3,000+ questions) | Planned |
| 10 | Exam blueprints & exports | Planned |
| 11 | Production hardening | Planned |

> **No performance, accuracy or throughput numbers exist yet.** Phase 0 records design budgets
> only; Phase 1 produces the first measured benchmarks on real hardware.

## Phase 0 deliverables

* [`docs/phase0/01_requirements_specification.md`](docs/phase0/01_requirements_specification.md) — MVP vs Production vs Future requirements
* [`docs/phase0/02_architecture.md`](docs/phase0/02_architecture.md) — stack, system diagram, end-to-end data flow, deployment topology
* [`docs/phase0/03_database_erd.md`](docs/phase0/03_database_erd.md) — full ERD, constraints, indexing & scale notes
* [`docs/phase0/04_llm_and_rag_choices.md`](docs/phase0/04_llm_and_rag_choices.md) — 8 GB GPU model shortlist, benchmark procedure, embedding & vector-DB decisions
* [`docs/phase0/05_retrieval_rag_generation_validation.md`](docs/phase0/05_retrieval_rag_generation_validation.md) — internet retrieval governance, RAG, knowledge packs, planner, validation matrix, math/physics/chemistry strategy
* [`docs/phase0/06_security_api_structure.md`](docs/phase0/06_security_api_structure.md) — security architecture, prompt-injection defenses, REST API surface, repo layout
* [`docs/phase0/07_risks_roadmap_acceptance.md`](docs/phase0/07_risks_roadmap_acceptance.md) — performance budgets, risk register, roadmap, acceptance criteria per phase
* [`docs/adr/README.md`](docs/adr/README.md) — ADR-001…ADR-010
* [`docs/phase0/SIGNOFF.md`](docs/phase0/SIGNOFF.md) — stakeholder checklist gating Phase 1

## Headline design decisions (summary)

* **Runtime:** llama.cpp `llama-server` with GGUF 4-bit quantization + GBNF grammar-constrained
  JSON; `ILLMProvider` abstraction keeps Ollama / any OpenAI-compatible server swappable.
* **Model shortlist for 8 GB VRAM:** Qwen2.5-7B-Instruct q4_K_M (default hypothesis),
  Gemma-2-9B-it, Llama-3.1-8B-Instruct, Phi-3.5-mini fallback tier — final pick by Phase-1
  benchmark, never by parameter count alone.
* **Data:** PostgreSQL 16 (own schema, millions-of-questions ready) + read-only adapter over the
  university's MySQL/MariaDB master data; Redis + RQ workers; checkpoint-based resumable jobs.
* **RAG:** local documents (+ optional allowlisted web sources via fetch-guard proxy) →
  semantic chunking → sentence-transformer embeddings → Qdrant (FAISS fallback adapter) →
  Topic Knowledge Packs mediate all generation prompts.
* **QA pipeline:** structural → independent answer verification (LLM re-check + SymPy where
  computable) → distractor validation → LaTeX/mhchem/unit validation → 4-level deduplication →
  difficulty/Bloom classification → composite quality score → option-position balancing →
  mandatory human approval into the bank.
* **Notation:** MathJax-compatible LaTeX stored as text (inline `\(..\)`, display `$$..$$`);
  mhchem optional behind a deployment flag; images forbidden; malformed notation cannot reach
  APPROVED.
* **Security:** LAN-only TLS API, internal-network LLM endpoints, JWT + RBAC
  (Administrator / QuestionGenerator / AcademicReviewer / SubjectExpert / ExamController /
  ReadOnly), append-only audit trail, SSRF/prompt-injection defenses, source-governance fields.

## Planned repository layout

```
backend/ (api, domain, database, legacy_adapter, jobs, retrieval, rag, generation,
          validation, review, export, blueprint, security, observability)
llm/     (providers, prompts, schemas, grammars)
frontend/ (React + TS + MathJax)
benchmarks/ deployment/ scripts/ docs/ tests/
```

## License

MIT — see [LICENSE](LICENSE).
