# TrustGraph

**Conflict-aware temporal memory for agents, built on HydraDB.**
Every fact is a claim with provenance and a validity window; contradictions
and overwrites are first-class graph structure; a graph-probe router answers
simple questions cheaply, escalates conflicted ones, and abstains when the
graph can't back an answer.

Hack Hydra 2026 (Aug 12–20), Track 3 — memory and context retrieval.
Build plan: [PLAN.md](PLAN.md).

## Quickstart

Requires Python 3.11+ and Docker (for the local HydraDB node).

```bash
pip install -r requirements.txt

# 1. Start a local HydraDB node (HTTP on 8443, Bolt on 7687)
bash scripts/dev-up.sh

# 2. Verify HydraDB supports every Cypher feature this project needs
python -m trustgraph.schema --verify

# 3. Generate the synthetic benchmark data (deterministic)
python -m trustgraph.generate

# 4. Ingest a scenario into HydraDB
python -m trustgraph.ingest data/sessions/deadline_drift.json
```

With an LLM endpoint configured (`LLM_API_KEY`, optionally `LLM_BASE_URL`
and `LLM_MODEL` — defaults are Moonshot/Kimi), the extraction pipeline:

```bash
# Local llama.cpp example (start `llama-server` on an OpenAI-compatible port)
# LLM_BASE_URL=http://127.0.0.1:8311/v1 LLM_API_KEY=sk-local LLM_MODEL=qwen3-8b

# Extract claims offline (no HydraDB needed) and score them against ground truth
python -m trustgraph.extract data/sessions/deadline_drift.json --emit drafts.json
python -m trustgraph.evaluate data/sessions/deadline_drift.json drafts.json

# Or run the full pipeline: extract -> reconcile -> write into HydraDB
python -m trustgraph.pipeline data/sessions/deadline_drift.json

# Ask questions (routes via the graph probe; no LLM key needed at query time)
python -m trustgraph.ask "What is the current launch deadline?" --verbose
```

Run the offline test suite (no HydraDB needed):

```bash
python -m pytest tests/
```

## Repo map

- `trustgraph/schema.cypher` — the graph model and canonical queries
  (current truth, time travel, supersession chains, conflicts, coverage)
- `trustgraph/db.py` — HydraDB client over the HTTP JSON query API
- `trustgraph/claims.py` — closed predicate vocabulary + ground-truth validation
- `trustgraph/generate/` — deterministic synthetic session generator
  (scripted overwrites, cross-source contradictions, abstention probes)
- `trustgraph/ingest.py` — writes a scenario into HydraDB as the
  claim/evidence graph (idempotent, batched `UNWIND`)
- `trustgraph/llm.py` + `trustgraph/extract.py` — LLM claim extraction
  (grounded quotes, closed predicate vocab, overwrite linking)
- `trustgraph/reconcile.py` — deterministic supersede/contradict/dedup rules
- `trustgraph/evaluate.py` — claim-level precision/recall vs. ground truth
- `trustgraph/pipeline.py` — extract → reconcile → write, per session
- `trustgraph/probe.py` + `trustgraph/router.py` — two-stage routing
  (classify → graph probe → FAST / DEEP / ABSTAIN)
- `trustgraph/scoring.py` — predicate-specific trust scoring for conflicts
- `trustgraph/retrieve.py` + `trustgraph/ask.py` — retrieval paths,
  deterministic cited answers, and the demo CLI
- `docs/tasks/` — self-contained execution specs for the remaining work
  (benchmark harness, LongMemEval loader, demo polish)
- `trustgraph/schema.py --verify` — probes a live node for the exact
  OpenCypher features this project depends on
- `scripts/dev-up.sh`, `docker-compose.yml` — local single-node HydraDB
- `demo/build-video.sh` — reproducible demo-video build (cards + live capture)

## How HydraDB is used

HydraDB is the system of record for agent memory, not a cache:

- **Typed relationships** model what vector memory re-derives at read time:
  `SUPERSEDES` (overwrite history), `CONTRADICTS` (unresolved conflicts),
  `ABOUT`, `SUPPORTED_BY`, `FROM` (provenance to source and evidence).
- **Property predicates** implement bitemporal reads: *"what was believed
  as of T"* is a `recorded_at <= T AND (valid_to IS NULL OR valid_to > T)`
  filter, not an inference over retrieved chunks.
- **Bounded variable-length paths** (`SUPERSEDES*1..5`) reconstruct the
  chronology of an overwritten fact for timeline answers.
- **Batched `UNWIND` writes** land extracted claims, evidence, and edges.

Without HydraDB, conflict detection, time-travel queries, and
typed-coverage abstention would all require ad-hoc scans over a document
store.

## Attribution

- [HydraDB](https://github.com/hydra-db/hydradb) — graph database (AGPL v3),
  used as a server via its HTTP query API; not modified or redistributed.
- [LongMemEval](https://github.com/xiaowu0162/LongMemEval) — benchmark data and
  question format (MIT License), used as a converter source; dataset files are
  not copied into or redistributed from this repository.
- Benchmark shape follows LongMemEval (Wu et al., arXiv:2410.10813).

## License

MIT — see [LICENSE](LICENSE).

## Results

Synthetic conflict suite, **25 questions across 8 scenarios** (oracle ground-truth
ingestion). Run the harness described in `docs/tasks/T1-benchmark-harness.md`:

```bash
python -m trustgraph.benchmark data/sessions/*.json --arm all
```

| Arm | Overall accuracy | Knowledge-update accuracy | Abstention P/R | Mean queries/question | p95 latency |
|---|---|---|---|---|---|
| Naïve RAG (top word-overlap claim) | 0.240 | 1.000 | 0.000 / 0.000 | 0.9 | 3.7 ms |
| Question Router | 0.760 | 1.000 | 1.000 / 0.500 | 4.8 | 71.3 ms |
| Always Deep | 0.840 | 1.000 | 1.000 / 0.500 | 5.0 | 70.1 ms |
| Router + Graph Probe | **1.000** | 1.000 | **1.000 / 1.000** | 4.7 | 71.1 ms |

The naïve RAG baseline picks the single active claim with the most word overlap
with the question. It cannot see supersession chains, cannot surface conflicts,
and guesses on every abstention question — dropping to 24% accuracy. The graph
probe gives TrustGraph precise, typed coverage: it abstains when no claim backs
the question, escalates to deep retrieval when conflicts or overwrites exist,
and answers cheaply only when the graph is clean.

**Why HydraDB?** The typed edges (`SUPERSEDES`, `CONTRADICTS`, `ABOUT`) and
property bitemporal filters are the reason the probe is cheap and exact. A
flat chunk store would have to re-derive chronology, conflict, and coverage at
query time; here they are materialized graph structure.

**LongMemEval**: we piloted the oracle subset; TrustGraph's closed predicate
vocabulary is tuned for structured project-memory claims and does not cleanly
extract open-ended personal-dialogue facts with the local 8B model. We therefore
report the scaled synthetic suite above as the primary ablation, which exercises
the same five LongMemEval abilities (IE, multi-session, temporal, knowledge
update, abstention) in the system's intended domain. To try the converter on the
real data, download `longmemeval_oracle.json` from the
[LongMemEval HuggingFace dataset](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned)
and run `python -m trustgraph.longmemeval convert <file> --out data/longmemeval/scenarios`.

End-to-end extraction from a local llama.cpp backend scores
P=1.000 / R=1.000 / F1=1.000 on the deadline-drift scenario (see
`python -m trustgraph.evaluate`). Set `LLM_TIMEOUT` (default 600 s) for
slow local backends; the extraction section of `scripts/demo.sh` runs this
live when `LLM_API_KEY` is set.

## Recording the demo video

A rendered demo video is available at **`demo/trustgraph-demo.mp4`** (~98 s, 1920×1080).
It covers the same beats as the checklist below. Rebuild it reproducibly with:

```bash
bash demo/build-video.sh     # needs ffmpeg + Docker; set LLM_API_KEY for the
                             # live extraction/evaluation section
```

The build script regenerates the title/benchmark cards (`demo/gen-cards.py`),
captures the live `scripts/demo.sh` and benchmark output as UTF-8 text, renders
the terminals with `demo/term-video.py`, and concatenates the sections.

- [x] 0:00–0:20 — Problem: facts change; flat memory returns stale + conflicting chunks.
- [x] 0:20–0:50 — Graph model: claims, evidence, SUPERSEDES/CONTRADICTS (show the graph).
- [x] 0:50–1:50 — Live demo: run `scripts/demo.sh`.
- [x] 1:50–2:30 — Benchmark table + ablation numbers (accuracy vs tokens/latency).
- [x] 2:30–3:00 — Why HydraDB: typed edges, property-filtered bitemporal queries, bounded `SUPERSEDES*1..n` traversal, `algo.*paths`; what the project would lose without it.

Keep the final video under 3 minutes and ensure it is legible without audio.
