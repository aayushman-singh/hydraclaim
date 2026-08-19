# HydraClaim — Hack Hydra (Track 3) Build Plan

**Hackathon:** Hack Hydra, Aug 12–20, 2026. Submissions close **Aug 20, 11:59 PM PT**
(form + ≤3-min demo video + public GitHub repo). Today is Aug 13 → **7 days left**.
**Track:** 03 — Memory and context retrieval ("Make your own mem0, and ace the benchmarks").
**Eligibility notes:** repo must have no commits before Aug 12, 2026 (fresh repo — do NOT
reuse HackHound, it has 2025 history); open-source license required; README must explain
how HydraDB is used; HydraDB itself is AGPL v3 (using it as a server over Bolt/HTTP is fine).

## One-liner

> **HydraClaim is conflict-aware temporal memory for agents: every fact is a claim with
> provenance and a validity window, contradictions and overwrites are first-class graph
> structure in HydraDB, and a graph-probe router answers simple questions cheaply,
> escalates conflicted ones, and abstains when the graph can't back an answer.**

It answers two questions flat memory can't: *"What is true now?"* and *"What was believed
last week?"* — and says "I don't know" when it should.

## What should change (fills gaps 1–2; 3 was already drafted)

1. **Cut connectors and UI; ship one ingestion path.** Synthetic Slack / Linear / meeting
   event streams (JSON) → LLM claim extractor → HydraDB. No OAuth, no live integrations,
   no web app beyond a thin demo surface (CLI or one-page chat). Connectors are plumbing
   judges won't credit; the graph is what they grade.
2. **Make abstention a designed, measurable behavior — not a prompt trick.** Abstain when
   the probe finds zero claims for the asked (subject, predicate), and report what was
   searched. This maps directly to LongMemEval's abstention split and to the track's
   "mostly fail at abstention" callout.
3. *(already drafted)* Cost-aware retrieval routing + benchmarking — positioned as the
   optimization that proves the system is practical, never the headline.

## Graph schema (completes the draft fragment)

```cypher
(:Entity   {id, name, type, aliases})                    // people, projects, systems
(:Claim    {id, predicate, value,                        // atomic assertion
            valid_from, valid_to,                        // event time (bitemporal)
            recorded_at,                                 // ingestion time
            status,                                      // active | superseded | disputed
            confidence})
(:Evidence {id, quote, ts, session_id, msg_id,
            extraction_confidence, explicitness})
(:Source   {id, kind, author, channel})                  // kind: slack | linear | meeting

(Claim)-[:ABOUT]->(Entity)
(Claim)-[:SUPPORTED_BY]->(Evidence)   (Evidence)-[:FROM]->(Source)
(Claim)-[:SUPERSEDES {at}]->(Claim)                      // explicit overwrite, new → old
(Claim)-[:CONTRADICTS {resolved}]->(Claim)               // unresolved conflict
```

- Small closed predicate vocabulary (~12–15): `owned_by`, `assigned_to`, `status`,
  `deadline`, `decided`, `depends_on`, `blocks`, `reports_to`, `located_in`, …
- Bitemporal reads: *as of T* = `recorded_at <= T AND (valid_to IS NULL OR valid_to > T)`
  — plain property predicates, which HydraDB's OpenCypher subset supports.
- Chronology of an overwritten fact: `MATCH p=(c:Claim)-[:SUPERSEDES*1..5]->(:Claim)`
  (bounded variable-length — supported).
- **Never overwrite.** New information creates a new Claim + SUPERSEDES edge; old claim
  gets `valid_to` set and `status='superseded'`.

## Two-stage router (completes the cut-off)

**Stage 1 — classify (one cheap LLM call):** extract
`{subject, predicate, time_scope, question_type}` where type ∈
`lookup | temporal | conflict-prone | synthesis`.

**Stage 2 — graph probe (2–3 bounded queries, no LLM):**
- `coverage` = #claims matching (subject, predicate)
- `conflicts` = #unresolved CONTRADICTS edges / #distinct active values
- `depth` = SUPERSEDES chain length

```text
coverage == 0                                   → ABSTAIN (say what was searched)
conflicts == 0 AND depth <= 1 AND type = lookup → FAST: 1 Cypher query → short answer + citations
otherwise                                       → DEEP: pull conflict subgraph
                                                  (claims + evidence + SUPERSEDES*1..5 chain)
                                                  → trust scoring → answer + timeline + citations
```

Defensibility: routing is driven by **measured graph state** (coverage, conflict count,
chain depth) — inspectable and benchmarkable — not by question phrasing alone.

## Trust scoring (replaces fixed per-source weights)

```text
score(claim, q) = 0.35 * authority(source.kind, q.predicate)
                + 0.20 * recency_decay(now − claim.valid_from)
                + 0.20 * author_authority(evidence.author, q.predicate)
                + 0.15 * explicitness(evidence)          // "moved to Fri" > "aim for Fri"
                + 0.10 * extraction_confidence(evidence)
```

- `authority()` is a **per-(source-kind, predicate) table**: Linear is authoritative for
  `assigned_to`/`status`, meeting notes for `decided`, Slack weak for `deadline` unless
  the author is the current owner. Show this table in the README — it's the concrete
  answer to "why not fixed weights".
- **Supersession always beats score.** Scoring only arbitrates true conflicts: two active
  claims, different values, no SUPERSEDES edge between them.

## Positioning claims that survive attack

Replace the absolutes with:

- "Vector memory retrieves text and re-derives state at read time. HydraClaim stores state
  *transitions* (SUPERSEDES, CONTRADICTS) as structure, so 'what is true now / at time T'
  is a query, not an inference over retrieved chunks."
- "Routing is driven by measured graph state — conflict count, evidence coverage,
  supersession depth — rather than inferred from question phrasing alone."
- "HydraClaim abstains on typed-predicate coverage: if no claim exists for the asked
  (subject, predicate) it declines and says what it searched. Embedding thresholds
  approximate this; the graph version is exact per predicate and explainable."

## Benchmarks

Track 3's numbers (30–40 sessions, ~115k tokens/question; chronology, overwrites,
abstention) mirror **LongMemEval** (Wu et al., arXiv:2410.10813): 500 questions, five
abilities — IE, multi-session, temporal, **knowledge update**, **abstention**.

- **Primary:** LongMemEval subset (~100–150 questions, stratified across the five types;
  ingestion at ~115k tokens/question is the cost driver, so subset + cache ingestion).
- **Differentiator (the graph-native story):** own synthetic conflict suite — 30–40
  sessions with scripted overwrites, cross-source contradictions (Slack vs Linear vs
  meeting), and unanswerable probes. Commit the generator so it's reproducible.
- **Arms (ablation table):**

  | Arm                 | Description                                   |
  |---------------------|-----------------------------------------------|
  | Always Deep         | retrieve + reason over all related claims     |
  | Question Router     | route from question classification only       |
  | Router + Graph Probe| classify → inspect conflicts/coverage → route |
  | Flat-vector reference (optional) | same LLM, embeddings over session chunks |

- **Metrics:** overall + per-type accuracy; knowledge-update accuracy; abstention
  precision/recall; tokens/question; retrieval queries/question; p50/p95 latency.
- Judges: *"working, thoughtful products, not just benchmark scores"* — benchmarks are
  the proof slide, the demo is the product.

## 7-day schedule (Aug 13 → Aug 20)

| Day | Work |
|-----|------|
| **D1 — today** | Fresh public repo + license + README skeleton. HydraDB spike via Docker (`ghcr.io/hydra-db/hydradb:latest`): verify the exact Cypher features needed — batched `UNWIND` writes, `SUPERSEDES*1..5`, `OPTIONAL MATCH`, property range filters. Freeze schema + scope. Start synthetic session generator. |
| **D2** | Ingestion: session JSON → LLM claim extractor (closed predicate vocab) → entity resolution (alias map) → SUPERSEDES/CONTRADICTS detection → batched writes. |
| **D3** | Retrieval: fast path, deep path, probe queries, abstention. CLI demo loop end-to-end. |
| **D4** | Eval harness. Synthetic suite first run. LongMemEval loader + small run. Fix the failure modes this exposes (usually extraction noise). |
| **D5** | Ablations → numbers. Demo surface polish. README (HydraDB-usage section is a submission requirement). |
| **D6** | **Feature freeze.** Full eval runs, tables/plots. Demo video rough cut. |
| **D7** | Buffer. Final video ≤3 min, repo cleanup, verify all links. **Submit hours early.** |

Explicit cut list: OAuth connectors, live sync, multi-user, real UI, entity linking beyond
an alias table, entering a second track.

## Demo video beats (≤3 min)

1. **0:00–0:20** Problem: facts change; flat memory returns stale + conflicting chunks.
2. **0:20–0:50** Graph model: claims, evidence, SUPERSEDES/CONTRADICTS (show the graph).
3. **0:50–1:50** Live: (a) simple lookup → fast path; (b) "Who owns the payments
    integration?" → conflict detected → deep path → answer with timeline + citations;
    (c) unanswerable → abstains, states what it searched; (d) "What was the deadline last
    week?" → time-travel query.
4. **1:50–2:30** Benchmark table + ablation numbers (accuracy vs tokens/latency).
5. **2:30–3:00** Why HydraDB: typed edges, property-filtered bitemporal queries, bounded
    `SUPERSEDES*1..n` traversal, `algo.*paths`; what the project would lose without it
    (required talking point).

## Risks

- **HydraDB OpenCypher is a subset** → the D1 spike is the derisk; don't skip it.
- **LLM extraction quality** is the biggest accuracy risk → store the raw quote on every
  Evidence node; deep path can answer from evidence even if claim structure is noisy.
- **LongMemEval ingestion cost/time** at 115k tokens/question → subset, cache, budget cap.
- **UI sink** → CLI or single-page app only; zero points are awarded for chrome.
