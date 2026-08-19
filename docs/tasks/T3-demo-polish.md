# T3 — Demo polish (D5)

Branch: `task/demo-polish`. Read `docs/tasks/README.md` first.

## Goal

The demo proves the headline (conflict-aware temporal memory) live. Build the
repeatable demo driver and finish the README's results section so recording
the 3-minute video is mechanical. PLAN.md "Demo video beats" is the script.

## 1. Create: `scripts/demo.sh`

`set -euo pipefail`, run from repo root (`cd "$(dirname "$0")/.."`). Sections,
each with a printed banner:

1. `python -m hydraclaim.generate` (fresh deterministic data).
2. If `docker` is available: `bash scripts/dev-up.sh` (idempotent) — otherwise
   print "Docker not available; skipping live sections" and exit 0.
3. Reset the graph so the demo is repeatable: HTTP DELETE is NOT available —
   instead use `python -c` with `hydraclaim.config.connect()` running
   `MATCH (n) DETACH DELETE n` (wrap in a comment noting it is demo-only).
4. Oracle ingestion of both scenarios:
   `python -m hydraclaim.ingest data/sessions/payments_owner_conflict.json
   data/sessions/deadline_drift.json`.
5. The four demo questions from PLAN.md, run with `python -m hydraclaim.ask
   ... --verbose`, each preceded by a printed header:
   - `Who owns the payments integration?`        (conflict → DEEP)
   - `What is the current launch deadline?`      (knowledge update → DEEP, chain)
   - `What was the launch deadline last week?`   (temporal → DEEP, as-of)
   - `What is the payments integration's uptime SLA?` (→ ABSTAIN)
6. Optional (behind `[ -n "${LLM_API_KEY:-}" ]`): run
   `python -m hydraclaim.extract data/sessions/deadline_drift.json --emit
   /tmp/tg-drafts.json` and `python -m hydraclaim.evaluate
   data/sessions/deadline_drift.json /tmp/tg-drafts.json` to show extraction
   quality live.

`bash -n scripts/demo.sh` must be clean. Keep it POSIX-ish (Git Bash on
Windows runs it).

## 2. Edit: `hydraclaim/ask.py` (allowed edit)

Add `--repl`: after handling flags, loop `input("hydraclaim> ")` until EOF or
empty line, calling the same answer path per question (reuse one db
connection for the loop). Keep the single-question behavior unchanged. No new
imports beyond stdlib.

## 3. Edit: `README.md` (allowed edit)

Append two sections:

- `## Results` — a placeholder table with the three benchmark arms as rows
  and columns: overall accuracy, knowledge-update accuracy, abstention P/R,
  mean queries/question, p95 latency. Fill every cell with `TBD (run T1)` and
  a one-line note pointing at `docs/tasks/T1-benchmark-harness.md`.
- `## Recording the demo video` — the five beats from PLAN.md as a checklist
  with timestamps (0:00–0:20 problem, 0:20–0:50 graph model, 0:50–1:50 live
  demo via `scripts/demo.sh`, 1:50–2:30 benchmark table, 2:30–3:00 why
  HydraDB), plus a reminder that the video must be ≤ 3 minutes and legible
  without audio.

## Acceptance

- `bash -n scripts/demo.sh` clean; `python -m pytest tests/ -q` green;
  `python -m hydraclaim.ask --help` shows `--repl`.
- `git diff main --stat` shows: new `scripts/demo.sh`, edits to
  `hydraclaim/ask.py` and `README.md`, and this task file marked done.

## Report back

The demo.sh section list, and a paste of the README Results table.

- [x] DONE (executing agent: check when merged-ready)
