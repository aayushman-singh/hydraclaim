# HydraClaim — Live demo script (optimal order)

This is the tightest 3–4 minute story, end to end, against the live API. It proves
the headline: *agents forget what changed and when — HydraClaim tracks it, surfaces
conflicts, and abstains when it can't answer.*

> Live app: https://hydraclaim.aayushman.dev
> API: https://hydraclaim-api.aayushman.dev

## 0. Pre-flight (before the audience)

```bash
# Backend + graph healthy
curl -s https://hydraclaim-api.aayushman.dev/health          # {"status":"ok"}
curl -s https://hydraclaim-api.aayushman.dev/graph | head -c 200

# Suggestions endpoint (DeepSeek picks 4 grounded questions)
curl -s https://hydraclaim-api.aayushman.dev/suggestions
```

Open the app, land on the **Console → Ask** tab. Hard-refresh so the newest build loads.

## Local CLI demo

Install the release package before you run the local workflow:

```bash
pip install hydraclaim
hydraclaim generate
hydraclaim ingest data/sessions/deadline_drift.json
hydraclaim ask "What is the current launch deadline?" --verbose
hydraclaim benchmark data/sessions/*.json --arm all
```

On Windows, verify a clean installation from the exact release wheel:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify-package.ps1
```

---

## 1. The router trace (30s) — Ask a hard question

Click the chip **"Who owns the payments integration?"** (or type it).

- Watch the **ROUTER TRACE** panel on the right populate step by step:
  classification → typed coverage → contradiction edge → supersession depth → **route: DEEP**.
- The answer card surfaces the **unresolved conflict** (both owners, trust-scored).
- Click a citation to expand it → the verbatim quote, claim id, validity window.

*Why this lands:* it shows the graph *measuring* graph state, not guessing from
phrasing. There is a real contradiction in the data and the system refuses to pick.

## 2. Four routes in one screen (30s) — the model's core claim

Hit the other chips to contrast the routes:

| Chip / question | Route | What it shows |
|---|---|---|
| "What is the current launch deadline?" | DEEP | supersession chain (2 overwrites) |
| "Where is Casey Brooks located now?" | FAST | cheap single-claim read |
| "What is Casey Brooks phone number?" | ABSTAIN | refuses + reports the gap |
| "Who owns the payments integration?" | DEEP | unresolved contradiction |

## 3. Claim graph + minimap (20s)

Open **Graph**.
- Dots = claims, the box nodes = entities, colors = active/superseded.
- **Minimap** top-left: white POV rectangle tracks your view — zoom/pan and it shrinks/moves.
- Legend top-right. Hit **Fit** to reset.

## 4. Live ingestion (60s) — the full write loop (the "wow")

Open **Ingest → Slack export**, drop in
`data/samples/slack-launch-demo.json`, click **Ingest**.

Expected result:

```
Ingestion complete:
  created: 3
  superseded: 1     <- Dario supersedes Priya as owner
  contradicted: 0
```

Then go back to **Ask** and type **"Who owns the launch sprint?"** →

```
FAST | Q3 launch sprint — owned_by: Dario Kim
     (as of ..., per slack/Dario Kim: "taking over the launch sprint from Priya")
```

*Why this lands:* the data you just pushed in is instantly queryable — extraction →
reconcile → write → graph → answer, all live. The `superseded: 1` is the overwrite
edge your question just followed.

Also show **Raw text** tab → paste `data/samples/sample-notes.txt` → Ingest → ask it.

## 5. Cost & determinism (10s)

In the Ask trace, the **COST** panel shows real graph-query count + latency. Note:
*retrieval is deterministic — the only model call is question classification; the
answer path is pure graph traversal.*

---

## Demo cheatsheet — the 4 questions that always fire the right route

| Question | Route |
|---|---|
| What is the current launch deadline? | DEEP (chain) |
| Who owns the payments integration? | DEEP (conflict) |
| Where is Casey Brooks located now? | FAST |
| What is Casey Brooks phone number? | ABSTAIN |

## Talking points when someone asks "why not vector RAG?"

1. **Bitemporal**: "what was true as of T" is a `recorded_at`/`valid_to` filter, not
   re-derived ranking.
2. **Supersession is materialized**: an overwrite is a `SUPERSEDES` edge, so history
   is a bounded traversal.
3. **Abstention is typed coverage**, not a similarity threshold — absent ≠ distant.
4. **Conflict is a first-class node relation**, so the system surfaces disagreement
   instead of averaging it away.
5. **Cost is bounded**: probe = 2–3 Cypher queries; the benchmark shows router+graph-probe
   at **0.980 accuracy, 4.7 queries/question, p95 ~733ms**.

## Rate limits (so you don't trip on the demo)

Per IP per hour (raiseable, but set to protect the DeepSeek budget):

| Endpoint | Limit |
|---|---|
| `/ask` | 60/hr |
| `/ingest` + `/ingest/slack` | 8/hr |
| `/suggestions` | 20/hr |

Watch the "API live" pill in the console top bar. If you hit a 429 mid-demo, say
*"hmm, rate limit — the system is protecting its model budget,"* and move on — it's
actually a feature to mention.
