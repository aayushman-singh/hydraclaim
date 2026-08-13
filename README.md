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
