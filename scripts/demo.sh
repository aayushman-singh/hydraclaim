#!/usr/bin/env bash
# HydraClaim D5 demo driver — repeatable, deterministic, Docker-aware.
set -euo pipefail
cd "$(dirname "$0")/.."

banner() {
  echo
  echo "================================================================================"
  echo "  $1"
  echo "================================================================================"
}

banner "1. Generate deterministic synthetic scenarios"
python -m hydraclaim.generate

banner "2. Start local HydraDB node"
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not available; skipping live sections"
  exit 0
fi
bash scripts/dev-up.sh

banner "3. Reset graph for a clean demo run"
# Demo-only: wipe the local graph so the demo is repeatable. Bare MATCH (n)
# is unsupported in this dialect, so delete per label.
python -c '
from hydraclaim.config import connect
db = connect()
for label in ("Claim", "Evidence", "Source", "Entity"):
    db.query(f"MATCH (n:{label}) DETACH DELETE n")
db.close()'

banner "4. Ingest oracle ground-truth claims"
python -m hydraclaim.ingest \
  data/sessions/payments_owner_conflict.json \
  data/sessions/deadline_drift.json

banner "5. Demo questions"

header() {
  echo
  echo "--- $1 ---"
}

header "Conflict: Who owns the payments integration?"
python -m hydraclaim.ask "Who owns the payments integration?" --verbose

header "Knowledge update: What is the current launch deadline?"
python -m hydraclaim.ask "What is the current launch deadline?" --verbose

header "Temporal: What was the launch deadline before the most recent change?"
python -m hydraclaim.ask "What was the launch deadline before the most recent change?" --verbose

header "Abstention: What is the payments integration's uptime SLA?"
python -m hydraclaim.ask "What is the payments integration's uptime SLA?" --verbose

banner "6. Optional live extraction evaluation (needs LLM_API_KEY)"
if [ -n "${LLM_API_KEY:-}" ]; then
  python -m hydraclaim.extract data/sessions/deadline_drift.json \
    --emit /tmp/tg-drafts.json
  python -m hydraclaim.evaluate data/sessions/deadline_drift.json \
    /tmp/tg-drafts.json
else
  echo "LLM_API_KEY not set; skipping extraction/evaluation section"
fi

banner "Demo complete"
