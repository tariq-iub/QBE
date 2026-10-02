# AI-QBE — Local LLM Strategy: Candidates, Embeddings, Vector DB (PHASE 0)

Covers deliverables #5 (LLM candidates for 8 GB GPU), #6 (embedding model), #7 (vector DB).

> **Honesty rule:** everything below is a *shortlist and selection procedure*. No accuracy,
> throughput, or VRAM number in this document is measured. Phase 1 executes the benchmark and
> produces the only numbers this project will quote.

---

## 1. Selection Constraints (hard)

* 8 GB VRAM ⇒ resident weights + KV cache + compute buffers ≤ ~7.4 GB usable.
  Practical envelope: **≤ 5.5 GB weights (q4_K_M class)** leaving ≥ 1.5–2 GB for KV cache at
  4k ctx + CUDA/llama.cpp overhead. Rule of thumb: q4_K_M ≈ 0.57–0.62 GB/B → fits up to
  ~8–9B params; 12–14B only via aggressive CPU offload (throughput collapse risk — measure).
* Bounded context windows (≤ 8k, default 4k): knowledge-pack prompts are compact by design.
* Structured JSON reliability is a first-class metric (grammar-constrained decoding mitigates
  but does not excuse weak instruction following).
* Academic breadth needed: math reasoning, physics units, chemistry notation, CS.

## 2. Generation-Model Shortlist (instruction-tuned, GGUF-available as of 2026)

| Model | Size / quant target | Why candidate | Watch-outs |
|---|---|---|---|
| **Qwen2.5-7B-Instruct** (and Qwen3-8B if grammar support validated) | 7B q4_K_M (~4.4 GB) | Strongest small-model math/science scores in its class; excellent JSON/instruction adherence; multilingual | Verify GBNF grammar sampling stability on chosen llama.cpp build |
| **Gemma-2-9B-it** | 9B q4_K_S (~5.1 GB) | Very good reasoning & fluency; strong distractor phrasing | Tighter VRAM margin; long-context KV heavier; Sliding-window attention quirks with some runtimes |
| **Llama-3.1-8B-Instruct** | 8B q4_K_M (~4.7 GB) | Robust general instruction following; huge ecosystem/tooling maturity | Weaker raw math than Qwen class; English-biased |
| **Mistral-Nemo-Instruct-2407 (12B)** | 12B q3_K_M or IQ3 (~6+ GB w/ partial offload) | Better depth for "Analyze" Bloom tier | Likely too heavy for full offload on 8 GB — benchmark as stretch candidate only |
| **Phi-3.5-mini (3.8B) / Qwen2.5-3B-Instruct** | q4/q5 (<3 GB) | Fast fallback; leaves room for verifier slot or bigger ctx | Distractor quality/recall ceiling lower; use as speed-comparison baseline |

**Verifier/Judge role:** initially the *same weights* as generator, separate prompt +
lower temperature + fresh context (independence comes from prompt/role and re-retrieved
evidence, not different architecture — documented limitation; Phase 1 may select a distinct
small model, e.g. Qwen2.5-3B, if VRAM allows a second slot or CPU inference suffices).

**Deployment default (pre-benchmark hypothesis, to be confirmed):**
`Qwen2.5-7B-Instruct q4_K_M, -ngl 99 (full offload), ctx 4096, temp 0.3 gen / 0.0 verify`.

## 3. Benchmark Procedure (Phase 1 execution plan)

Task set (~100 tasks, versioned in `benchmarks/tasks/`): 20 factual MCQ generations from
supplied evidence (Physics/Chem/Math/CS/Bio ×4), 10 LaTeX/mhchem-heavy items, 10 numeric
distractor-generation items, 15 verification judgments (with known ground truth incl.
planted wrong answers), 10 duplicate-judgment pairs (paraphrase band), 10 injection-resistance
probes (instructions embedded in evidence blocks), structured-output stress (batch sizes
10/20/30), plus knowledge-extraction trials. Metrics recorded per model×quant:

```
model, params, quant, file_size_gb, vram_peak_gb, ram_peak_gb, tokens_per_sec,
questions_per_min (end-to-end batch), json_schema_pass_rate, fact_pass_vs_key,
math_correctness (SymPy-checked), distractor_plausibility (expert rubric 1–5),
bloom_adherence, option_length_compliance, injection_refusal_rate
```

Hardware telemetry via `nvidia-smi` sampler + `/usr/bin/time`; report template ships with
Phase 1 (`benchmarks/REPORT_TEMPLATE.md`). Acceptance: chosen model must reach
**≥ 95 % schema pass under grammar constraint** and **≥ 85 % verification agreement with
expert keys** on the task set before Phase 5 gates open. Exact thresholds configurable.

## 4. Embedding Model Recommendation

Purpose: chunk retrieval, semantic dedup (L3), concept clustering. CPU-first operation.

| Candidate | Dims | Notes |
|---|---|---|
| **paraphrase-multilingual-mpnet-base-v2** *(initial default)* | 768 | Multilingual, robust, ~1.2 GB RAM, well-known behavior, fast enough on CPU |
| bge-m3 | 1024 | Stronger retrieval (dense+sparse+colbert); heavier; evaluate in Phase 3 |
| gte-large-en-v1.5 | 1024 | Top MTEB-en retrieval; English-only — matters only if banks stay English |
| nomic-embed-text-v1.5 | 768 | Long-input friendly; matryoshka dims allow cheap 256-d variant |

Decision gate (Phase 3): retrieve-on-labeled-set test (topic→relevant chunks) with
recall@5 / recall@10; dedup AUC on labeled paraphrase pairs. Default stays unless a
candidate wins both by >3 points absolute. Quantized vectors + int8 scalar quantization in
Qdrant evaluated for index size.

## 5. Vector Database Decision

**Chosen: Qdrant (single container, local mode).** Justification against requirements:

1. Spec demands *semantic relevance + metadata filters* together — Qdrant payload filtering
   (subject_id, topic_id, source_id, origin, quality_tier) is native and indexed. FAISS needs
   hand-rolled filter pre/post-processing code we'd own forever.
2. Dedup L3 = high-frequency kNN insert/query interleaved with generation — server API with
   async client suits worker pool better than reloading FAISS indices.
3. Operational fit: one stateless-ish container, snapshot backup trivial, memory footprint
   fine for expected scale (millions of 768-d vectors ≈ few GB — within 32 GB RAM budget;
   HNSW params tuned for recall/speed tradeoff in Phase 3).
4. **FAISS retained as justified fallback**: embedded mode adapter (`rag.store`) implements
   both backends; if ops review rejects another container on the workstation, FAISS+SQLite
   metadata satisfies MVP offline. Switch is config-level (ADR-004).

Collections: `chunks_v1` (retrieval), `stems_v1` (dedup L3) — separated because embedding
inputs differ (context chunk vs question stem normalization).

## 6. Model-Independence Contract (`ILLMProvider`)

```python
class ILLMProvider(Protocol):
    def generate(self, req: CompletionRequest) -> CompletionResult: ...   # sync single
    async def generate_batch(self, reqs: Sequence[CompletionRequest]) -> ...  # bounded parallel
    def health(self) -> ProviderHealth: ...                               # incl. VRAM stats
    # CompletionRequest carries: messages, response_schema (jsonschema), grammar: bool,
    # temperature/top_p/top_k/repeat_penalty/max_tokens/seed, timeout, purpose tag
```

Implementations: `LocalLlamaCppProvider` (OpenAI-compat endpoints + grammar param),
`OllamaProvider`, `OpenAICompatibleProvider` (any future local server). Schema validation
runs **after** provider return regardless of implementation ("never accept malformed JSON"
is enforced centrally in `llm.schemas`, not per-provider). Model registry rows bind
provider+model+quant so provenance survives swaps.
