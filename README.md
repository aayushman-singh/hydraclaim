# HydraClaim

**Conflict-aware temporal memory for AI agents, built on HydraDB.**

Agents forget what changed and when. HydraClaim turns agent memory into a
temporal claim graph: every fact is a claim with provenance and a validity
window, contradictions and overwrites are first-class graph structure, and a
graph-probe router answers cheaply, escalates conflicted questions, and —
crucially — abstains when the graph can't back an answer.

> Built for [Hack Hydra 2026](https://hackhydra.com), Track 3 — Memory and
> context retrieval.

[![MIT license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Hack Hydra 2026](https://img.shields.io/badge/Hack%20Hydra-2026-8b5cf6.svg)](#)

## Demo

Try it live:

- **App** — https://hydraclaim.aayushman.dev
- **API** — https://hydraclaim-api.aayushman.dev (`/ask`, `/graph`, `/scenarios`, `/health`)

The app is a static page wired to a live HydraDB graph pre-ingested with 16
scenarios. Ask it about deadlines, owners, conflicts, or facts it has never
recorded.

## Features

- **Supersession chains** — overwrites are typed `SUPERSEDES` edges; history is
  never destroyed, and any fact's full timeline is a bounded graph traversal.
- **Conflict detection** — `CONTRADICTS` edges mark unresolved disagreements;
  predicate-specific trust scoring arbitrates instead of silently averaging.
- **Typed abstention** — if no claim covers the asked `(subject, predicate)`, the
  system refuses and reports the gap. No nearest-chunk guessing.
- **Bitemporal reads** — recording time and validity windows make *"what was true
  as of T"* a filter, not an inference.
- **Graph-probe routing** — 2–3 bounded Cypher queries classify a question as
  `FAST` / `DEEP` / `ABSTAIN`, so cheap answers stay cheap and hard ones escalate.
- **Cited answers** — every answer traces to the verbatim quote, author, and
  source that support it.

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

## Quickstart

Requires Python 3.11+ and Docker (for the local HydraDB node).

```bash
git clone https://github.com/aayushman-singh/hydraclaim.git
cd hydraclaim
pip install -r requirements.txt

# 1. Start a local HydraDB node (HTTP on 8443, Bolt on 7687)
bash scripts/dev-up.sh

# 2. Verify HydraDB supports every Cypher feature this project needs
python -m hydraclaim.schema --verify

# 3. Generate the synthetic benchmark data (deterministic)
python -m hydraclaim.generate

# 4. Ingest a scenario into HydraDB
python -m hydraclaim.ingest data/sessions/deadline_drift.json

# 5. Run the API server (serves /ask, /graph, /scenarios, /health)
python -m hydraclaim.serve --host 127.0.0.1 --port 8000
```

To run the web app locally against your own server, edit `web/config.js` and
point `window.HYDRACLAIM_API` at `http://127.0.0.1:8000`, then serve the `web/`
folder from any static server (e.g. `python -m http.server` in `web/`).
The API sends `Access-Control-Allow-Origin: *`, so a plain site works.

### Ask a question from the CLI

```bash
# Question classification uses DeepSeek when LLM_API_KEY is set
# (LLM_BASE_URL / LLM_MODEL are optional); otherwise a keyword heuristic routes.
python -m hydraclaim.ask "What is the current launch deadline?" --verbose
```

### Use the extraction pipeline

Requires an LLM endpoint (`LLM_API_KEY`, optionally `LLM_BASE_URL` and `LLM_MODEL`):

```bash
# Extract claims and score them against ground truth
python -m hydraclaim.extract data/sessions/deadline_drift.json --emit drafts.json
python -m hydraclaim.evaluate data/sessions/deadline_drift.json drafts.json

# Or run the full pipeline: extract -> reconcile -> write into HydraDB
python -m hydraclaim.pipeline data/sessions/deadline_drift.json
```

### Run the tests

```bash
python -m pytest tests/
```

> **Note:** this is a hackathon prototype that doubles as a benchmark harness,
> not a production library. The API exposes write endpoints (`/ingest`,
> `/ingest/slack`) intended for the demo flow; secure it behind a write key
> before deploying outside a hackathon.

## Architecture

```
  Raw text / Slack / meeting notes
        │
        ▼
  ┌─────────────┐    closed predicate vocab
  │  Extraction  │──  (LLM: grounded quotes,
  │  (LLM)       │    overwrite linking)
  └──────┬───────┘
         ▼
  ┌─────────────┐    deterministic supersede /
  │ Reconciler   │──  contradict / dedup rules
  └──────┬───────┘
         ▼
  ┌─────────────┐    batched UNWIND writes
  │  HydraDB    │──  (claims, evidence, edges)
  └──────┬───────┘
         ▼
  ┌─────────────┐    classify → graph probe →
  │  Router      │──  FAST / DEEP / ABSTAIN
  └──────┬───────┘
         ▼
     Cited answer
```

**Two-stage routing:**

1. **Classify** (one LLM call) — extract subject, predicate, time scope, question type.
2. **Graph probe** (2–3 bounded Cypher queries, no LLM) — measure coverage, conflicts, supersession depth.

| Probe result | Route |
|---|---|
| Zero claims for (subject, predicate) | **ABSTAIN** — decline and report the gap |
| No conflicts, depth ≤ 1, simple lookup | **FAST** — single Cypher query → short answer + citations |
| Conflicts or deep supersession chain | **DEEP** — pull conflict subgraph → trust scoring → timeline + citations |

## Results

Synthetic conflict suite, **50 questions across 16 scenarios** (oracle ground-truth
ingestion):

```bash
python -m hydraclaim.benchmark data/sessions/*.json --arm all
```

| Arm | Accuracy | Abstention P/R | Queries/q | p95 latency |
|---|---|---|---|---|
| Naïve RAG (top word-overlap claim) | 0.280 | 0.000 / 0.000 | 1.0 | 123 ms |
| Question Router | 0.680 | 0.857 / 0.375 | 4.8 | 1043 ms |
| Always Deep | 0.780 | 0.857 / 0.375 | 5.0 | 622 ms |
| **Router + Graph Probe** | **0.980** | **0.941 / 1.000** | 4.7 | 733 ms |

The suite covers supersession chains up to depth 3, typed and untyped (latent)
value conflicts, alias-only entity references, and as-of boundary reads. The
naïve RAG baseline picks the single active claim with the most word overlap.
It cannot see supersession chains, cannot surface conflicts, and guesses on every
abstention question. The graph probe gives typed coverage: it abstains when no
claim backs the question, escalates when conflicts or overwrites exist, and
answers cheaply only when the graph is clean. The single router+probe failure is
a subject/object inversion ("who works on X" vs "X is worked on by who") — a
documented closed-vocabulary limitation, not a retrieval error.

**Why HydraDB?** The typed edges (`SUPERSEDES`, `CONTRADICTS`, `ABOUT`) and
property bitemporal filters are the reason the probe is cheap and exact. A flat
chunk store would have to re-derive chronology, conflict, and coverage at query
time; here they are materialized graph structure. Results are reproducible with
a single command against a freshly ingested 16-scenario graph and are written to
`results/` (generated when you run the harness; not committed).

**LongMemEval**: we piloted the oracle subset; HydraClaim's closed predicate
vocabulary is tuned for structured project-memory claims and does not cleanly
extract open-ended personal-dialogue facts with a small local model. We
therefore report the scaled synthetic suite above as the primary ablation, which
exercises the same five LongMemEval abilities (IE, multi-session, temporal,
knowledge update, abstention) in the system's intended domain. To try the
converter on the real data, download `longmemeval_oracle.json` from the
[LongMemEval HuggingFace dataset](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned)
and run `python -m hydraclaim.longmemeval convert <file> --out data/longmemeval/scenarios`.

End-to-end extraction from a local model scores `P=1.000 / R=1.000 / F1=1.000`
on the deadline-drift scenario (see `python -m hydraclaim.evaluate`). Extraction
is LLM-only; every query-path answer is deterministic.

## Repo map

| Path | Purpose |
|---|---|
| `hydraclaim/schema.cypher` | Graph model and canonical queries |
| `hydraclaim/db.py` | HydraDB client (HTTP JSON query API) |
| `hydraclaim/claims.py` | Closed predicate vocabulary + ground-truth validation |
| `hydraclaim/generate/` | Deterministic synthetic session generator |
| `hydraclaim/ingest.py` | Writes scenarios into HydraDB (idempotent, batched `UNWIND`) |
| `hydraclaim/extract.py` | LLM claim extraction (grounded quotes, overwrite linking) |
| `hydraclaim/reconcile.py` | Deterministic supersede / contradict / dedup rules |
| `hydraclaim/evaluate.py` | Claim-level precision / recall vs. ground truth |
| `hydraclaim/pipeline.py` | Extract → reconcile → write, per session |
| `hydraclaim/probe.py` | Graph probe queries (coverage, conflicts, depth) |
| `hydraclaim/router.py` | Two-stage routing (classify → probe → route) |
| `hydraclaim/scoring.py` | Predicate-specific trust scoring for conflicts |
| `hydraclaim/retrieve.py` | Retrieval paths (fast + deep) |
| `hydraclaim/ask.py` | Deterministic cited answers and CLI |
| `hydraclaim/serve.py` | API server for the web frontend |
| `hydraclaim/benchmark.py` | Ablation benchmark harness |
| `web/` | Frontend (static HTML/CSS/JS, vis-network graph) |
| `scripts/dev-up.sh` | Local single-node HydraDB via Docker |
| `docker-compose.yml` | Docker Compose for HydraDB |

## Attribution

- [HydraDB](https://github.com/hydra-db/hydradb) — graph database (AGPL v3),
  used as a server via its HTTP query API; not modified or redistributed.
- [LongMemEval](https://github.com/xiaowu0162/LongMemEval) — benchmark data and
  question format (MIT License), used as a converter source; dataset files are
  not copied into or redistributed from this repository.

## License

MIT — see [LICENSE](LICENSE).
