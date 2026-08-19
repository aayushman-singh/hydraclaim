"""Read-only demo web server for HydraClaim.

    python -m hydraclaim.serve --port 8000

Endpoints:
  GET  /health                -> {"status": "ok"}
  GET  /scenarios             -> scenario list with sample questions for the UI
  GET  /graph                 -> entity/claim nodes + edges for the graph view
  POST /ask {"question": str} -> retrieve.answer() result (route, answer,
                                 citations, classification, probe)

Stdlib HTTP only — no new runtime dependencies. Question classification uses
the LLM when LLM_API_KEY is set (any OpenAI-compatible endpoint via
LLM_BASE_URL/LLM_MODEL); otherwise the keyword heuristic classifies. On an
LLM error the router degrades to the heuristic (its designed behavior) and
the failure is logged loudly in the service journal — request routing never
silently breaks.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from hydraclaim import retrieve

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"


def llm_classifier(question: str) -> dict:
    """LLM question classification (grounded in the predicate vocabulary).

    hydraclaim.router.classify degrades to the keyword heuristic on LLM
    errors — that is its designed behavior — but the failure is logged loudly
    here with a traceback so it is never silent in the service journal.
    """
    from hydraclaim.router import llm_classifier as _classify

    try:
        return _classify(question)
    except Exception:
        import traceback

        print(f"ERROR: LLM classification failed for question {question!r} — "
              "degrading to keyword heuristic",
              file=sys.stderr, flush=True)
        traceback.print_exc()
        raise


def handle_ask(question: str, db, llm_fn) -> dict[str, Any]:
    result = retrieve.answer(db, question, llm_fn=llm_fn)
    return result


def handle_scenarios() -> dict[str, Any]:
    scenarios = []
    for path in sorted(DATA_DIR.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        scenarios.append({
            "id": doc["scenario_id"],
            "description": doc.get("description", ""),
            "questions": [qa["question"] for qa in doc["ground_truth"]["qa"]],
        })
    return {"scenarios": scenarios}


def handle_graph(db) -> dict[str, Any]:
    """Compact graph for visualization: entities + claims + relation edges."""
    entities = db.query("MATCH (e:Entity) RETURN e.id AS id, e.name AS name, "
                        "e.type AS type")
    claims = db.query("""
MATCH (c:Claim)-[:ABOUT]->(e:Entity)
RETURN c.id AS id, c.key AS key, e.name AS subject, c.predicate AS predicate,
       c.value AS value, c.status AS status, c.valid_from AS valid_from,
       c.valid_to AS valid_to""")
    edges: list[dict[str, Any]] = []
    for row in db.query("MATCH (a:Claim)-[:SUPERSEDES]->(b:Claim) "
                        "RETURN a.id AS src, b.id AS dst"):
        edges.append({"from": row["src"], "to": row["dst"], "type": "SUPERSEDES"})
    for row in db.query("MATCH (a:Claim)-[:CONTRADICTS]->(b:Claim) "
                        "RETURN a.id AS src, b.id AS dst"):
        edges.append({"from": row["src"], "to": row["dst"], "type": "CONTRADICTS"})
    entity_id_by_name = {e["name"]: e["id"] for e in entities}
    for claim in claims:
        edges.append({"from": claim["id"], "to": entity_id_by_name[claim["subject"]],
                      "type": "ABOUT"})
    nodes = ([{"id": e["id"], "label": e["name"], "kind": "entity",
               "type": e.get("type", "unknown")} for e in entities]
             + [{"id": c["id"], "label": f"{c['predicate']}: {c['value']}",
                 "kind": "claim", "key": c["key"], "subject": c["subject"],
                 "status": c["status"]} for c in claims])
    return {"nodes": nodes, "edges": edges}


def dispatch(method: str, path: str, body: dict, db, llm_fn) -> tuple[int, dict]:
    """Route a request to a handler. Separated from HTTP for offline tests."""
    if method == "GET" and path == "/health":
        return 200, {"status": "ok"}
    if method == "GET" and path == "/scenarios":
        return 200, handle_scenarios()
    if method == "POST" and path == "/ask":
        question = (body.get("question") or "").strip()
        if not question:
            return 400, {"error": "missing 'question' in request body"}
        from hydraclaim.db import HydraDBError
        try:
            return 200, handle_ask(question, db, llm_fn)
        except HydraDBError as exc:
            return 502, {"error": f"graph backend failed: {exc}"}
    if method == "GET" and path == "/graph":
        from hydraclaim.db import HydraDBError
        try:
            return 200, handle_graph(db)
        except HydraDBError as exc:
            return 502, {"error": f"graph backend failed: {exc}"}
    return 404, {"error": f"unknown endpoint: {method} {path}"}


class DemoHandler(BaseHTTPRequestHandler):
    """HTTP plumbing around dispatch()."""

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self) -> tuple[int, dict]:
        llm_fn = self.server.llm_fn  # type: ignore[attr-defined]
        try:
            if self.command == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            else:
                body = {}
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            return dispatch(self.command, path, body, self.server.db, llm_fn)  # type: ignore[attr-defined]
        except json.JSONDecodeError:
            return 400, {"error": "request body must be JSON"}

    def do_GET(self) -> None:
        self._respond(*self._dispatch())

    def do_POST(self) -> None:
        self._respond(*self._dispatch())

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # quiet: access logging goes to the service manager's journal


def main() -> None:
    parser = argparse.ArgumentParser(prog="hydraclaim.serve")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--llm", action="store_true",
                        help="use the LLM for question classification "
                             "(requires LLM_API_KEY); default is the keyword heuristic")
    args = parser.parse_args()

    import os
    from hydraclaim.config import connect

    llm_fn = llm_classifier if args.llm and os.environ.get("LLM_API_KEY") else None
    db = connect()
    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    server.db = db  # type: ignore[attr-defined]
    server.llm_fn = llm_fn  # type: ignore[attr-defined]
    mode = "LLM classification" if llm_fn else "heuristic classification"
    print(f"hydraclaim.serve listening on http://{args.host}:{args.port} ({mode})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        db.close()
        server.server_close()


if __name__ == "__main__":
    main()
