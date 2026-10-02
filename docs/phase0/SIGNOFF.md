# PHASE 0 Sign-off Checklist

Reviewer initials/date required before Phase 1 begins on target hardware.

| # | Item | Doc | Accept? |
|---|---|---|---|
| 1 | Requirements w/ MVP/PROD/FUT classification complete | 01 | ☐ |
| 2 | Tech stack accepted (FastAPI/Postgres/Redis+RQ/Qdrant/llama.cpp) | 02 §1 | ☐ |
| 3 | Architecture & data flow reviewed; generation≠validation enforced | 02 §2–4 | ☐ |
| 4 | ERD covers every entity listed in master prompt §5 + audit/scale notes | 03 | ☐ |
| 5 | LLM shortlist + benchmark procedure + acceptance floors agreed | 04 §1–3 | ☐ |
| 6 | Embedding default + Phase-3 evaluation gate agreed | 04 §4 | ☐ |
| 7 | Qdrant decision + FAISS fallback accepted | 04 §5, ADR-004 | ☐ |
| 8 | Internet retrieval policy (domains/tiers/SSRF/injection) accepted | 05 §1 | ☐ |
| 9 | Knowledge-pack mediation rule accepted | 05 §2, ADR-006 | ☐ |
| 10 | Planner/over-generation/batching rules accepted | 05 §3 | ☐ |
| 11 | Validation stage matrix + quality weights accepted | 05 §4 | ☐ |
| 12 | Notation strategy (LaTeX canonical, mhchem flag) accepted | 05 §5, ADR-007 | ☐ |
| 13 | Security architecture incl. roles, zones, injection defenses | 06 §1 | ☐ |
| 14 | REST API surface reviewed against §28 requirements | 06 §2 | ☐ |
| 15 | Directory structure adopted | 06 §3 | ☐ |
| 16 | Performance budgets understood **as budgets, not claims** | 07 §1 | ☐ |
| 17 | Risk register reviewed; owners assigned | 07 §2 | ☐ |
| 18 | Roadmap dependency order feasible for team size | 07 §3 | ☐ |
| 19 | Phase acceptance criteria agreed as gates | 07 §4 | ☐ |
| 20 | ADRs 001–010 ratified | docs/adr | ☐ |

Explicit non-claims confirmed by sign-off: no measured throughput/accuracy exists yet; model
final choice happens in Phase 1 on real hardware; NFR-4 overnight window is a budget to test,
not a promise.
