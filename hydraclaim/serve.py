"""HydraClaim web server with read and write paths.

    python -m hydraclaim.serve --port 8000

Endpoints:
  GET  /health                -> {"status": "ok"}
  GET  /scenarios             -> scenario list with sample questions for the UI
  GET  /graph                 -> entity/claim nodes + edges for the graph view
  POST /ask {"question": str} -> retrieve.answer() result (route, answer,
                                 citations, classification, probe)
  POST /ingest                -> LLM extract + reconcile + write (needs LLM_API_KEY)
  POST /ingest/slack          -> Slack export -> sessions -> ingest pipeline

Stdlib HTTP only — no new runtime dependencies. Question classification uses
the keyword heuristic by default. The --llm option selects LLM classification
and stops the request when the LLM fails.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from hydraclaim import retrieve
from hydraclaim.claim_read import ClaimReader, ClaimScope
from hydraclaim.db import HydraDBError
from hydraclaim.llm import LLMError
from hydraclaim.ratelimit import limiter
from hydraclaim.router import ClassificationError

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"
logger = logging.getLogger(__name__)


def _error(code: str, message: str) -> dict[str, str]:
    """Build the stable error shape returned by HTTP endpoints."""
    return {"code": code, "error": message}


class SuggestionResponseError(ValueError):
    """Raised when the suggestion LLM returns an invalid JSON shape."""


def _request_context(method: str, path: str, body: object, mode: str) -> str:
    """Return safe request context for remote-failure logs."""
    fields = sorted(body) if isinstance(body, dict) else []
    question = body.get("question") if isinstance(body, dict) else None
    question_length = len(question) if isinstance(question, str) else None
    return (
        f"method={method} endpoint={path} mode={mode} body_type={type(body).__name__} "
        f"fields={fields!r} question_length={question_length}"
    )


def _log_remote_failure(kind: str, context: str, exc: Exception) -> None:
    logger.exception(
        "%s failed %s exception_type=%s", kind, context, type(exc).__name__
    )


def llm_classifier(question: str) -> dict:
    """LLM question classification (grounded in the predicate vocabulary).

    Errors propagate to the request handler so the selected classification mode
    does not change during a request.
    """
    from hydraclaim.router import llm_classifier as _classify

    return _classify(question)


def handle_ask(
    question: str, db, llm_fn, classification_mode: str = "heuristic"
) -> dict[str, Any]:
    result = retrieve.answer(
        db, question, classification_mode=classification_mode, llm_fn=llm_fn
    )
    return result


def handle_scenarios() -> dict[str, Any]:
    scenarios = []
    for path in sorted(DATA_DIR.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        scenarios.append(
            {
                "id": doc["scenario_id"],
                "description": doc.get("description", ""),
                "questions": [qa["question"] for qa in doc["ground_truth"]["qa"]],
            }
        )
    return {"scenarios": scenarios}


# Route hint per gold question type for deterministic heuristic selection.
_QTYPE_TO_ROUTE = {
    "conflict": "CONFLICT",
    "abstention": "ABSTAIN",
    "temporal": "DEEP",
    "knowledge_update": "DEEP",
    "multi_session": "DEEP",
    "lookup": "FAST",
}

_SUGGESTION_SYSTEM = """You suggest demo questions for a conflict-aware temporal memory
system. You are given real ground-truth questions grouped by route:

- FAST: clean single-fact lookups
- DEEP: facts with overwrite history or an exact time in the past
- CONFLICT: two sources disagree (an unresolved contradiction)
- ABSTAIN: something the system has never recorded

Pick exactly one question per route (4 total), choosing the clearest, most
demo-friendly phrasing from each group. Expect route is a hint: pick the question
that will trigger it. Prefer short questions with a clear answer.

Respond with strict JSON only:
{"suggestions": [{"text": "", "route": "FAST"}, {"text": "", "route": "DEEP"},
                 {"text": "", "route": "CONFLICT"}, {"text": "", "route": "ABSTAIN"}]}"""


def _build_suggestion_payload(scenarios: list[dict]) -> dict[str, list[dict]]:
    """Group ground-truth questions by route bucket (deterministic baseline)."""
    by_route: dict[str, list[str]] = {}
    for doc in scenarios:
        for qa in doc.get("ground_truth", {}).get("qa", []):
            route = _QTYPE_TO_ROUTE.get(qa.get("qtype"), "DEEP")
            by_route.setdefault(route, []).append(qa["question"])
    return {
        "suggestions": [
            {"text": by_route[r][0], "route": r}
            for r in ("FAST", "DEEP", "CONFLICT", "ABSTAIN")
            if by_route.get(r)
        ]
    }


def handle_suggestions(llm_fn, *, suggestion_mode: str = "heuristic") -> dict[str, Any]:
    """Return 4 diverse demo questions (one per route), grounded in the data.

    The caller selects either deterministic heuristic selection or LLM selection.
    An LLM response error stops the request.
    """
    if suggestion_mode not in {"heuristic", "llm"}:
        raise ValueError(f"unknown suggestion mode: {suggestion_mode!r}")

    scenarios = []
    for path in sorted(DATA_DIR.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        scenarios.append(doc)

    baseline = _build_suggestion_payload(scenarios)
    if suggestion_mode == "heuristic" or not baseline["suggestions"]:
        return baseline
    if llm_fn is None:
        raise ValueError("llm suggestion mode requires llm_fn")

    from hydraclaim.llm import chat_json

    bucket_text = "\n".join(
        f"[{b['route']}] {b['text']}" for b in baseline["suggestions"]
    )
    result = chat_json(
        [
            {"role": "system", "content": _SUGGESTION_SYSTEM},
            {
                "role": "user",
                "content": "Ground-truth questions by route:\n" + bucket_text,
            },
        ]
    )
    if not isinstance(result, dict):
        raise SuggestionResponseError(
            f"suggestion response must be an object, got {type(result).__name__}"
        )
    suggestions = result.get("suggestions")
    if not isinstance(suggestions, list) or not suggestions:
        raise SuggestionResponseError(
            "suggestion response must contain a non-empty list"
        )

    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for index, suggestion in enumerate(suggestions):
        if not isinstance(suggestion, dict):
            raise SuggestionResponseError(
                f"suggestion response item {index} must be an object"
            )
        text = suggestion.get("text")
        route = suggestion.get("route")
        if not isinstance(text, str) or not text.strip():
            raise SuggestionResponseError(
                f"suggestion response item {index} has invalid text"
            )
        if not isinstance(route, str) or route not in _QTYPE_TO_ROUTE.values():
            raise SuggestionResponseError(
                f"suggestion response item {index} has invalid route"
            )
        if route in seen:
            continue
        seen.add(route)
        deduped.append({"text": text, "route": route})
    if not deduped:
        raise SuggestionResponseError("suggestion response has no unique routes")
    return {"suggestions": deduped}


def handle_graph(db) -> dict[str, Any]:
    """Compact graph for visualization: entities + claims + relation edges."""
    reader = ClaimReader(db)
    entities = list(reader.read_entities())
    claims = []
    edges: list[dict[str, Any]] = []
    for entity in entities:
        scope = ClaimScope(subject=entity["name"])
        selected = reader.read_claims(scope)
        claims.extend(selected)
        claim_ids = {claim.id for claim in selected}
        for relation_type in ("SUPERSEDES", "CONTRADICTS"):
            for row in reader.read_relations(scope, claim_ids, relation_type):
                if relation_type == "SUPERSEDES":
                    source = row.get("new_id", row.get("src"))
                    target = row.get("old_id", row.get("dst"))
                else:
                    source = row.get("a_id", row.get("src"))
                    target = row.get("b_id", row.get("dst"))
                edges.append(
                    {
                        "from": source,
                        "to": target,
                        "type": relation_type,
                    }
                )
    entity_id_by_name = {e["name"]: e["id"] for e in entities}
    for claim in claims:
        edges.append(
            {
                "from": claim.id,
                "to": entity_id_by_name[claim.subject],
                "type": "ABOUT",
            }
        )
    nodes = [
        {
            "id": e["id"],
            "label": e["name"],
            "kind": "entity",
            "type": e.get("type", "unknown"),
        }
        for e in entities
    ] + [
        {
            "id": c.id,
            "label": f"{c.predicate}: {c.value}",
            "kind": "claim",
            "key": c.key,
            "subject": c.subject,
            "status": c.status,
        }
        for c in claims
    ]
    return {"nodes": nodes, "edges": edges}


WRITE_KEY = os.environ.get("HYDRACLAIM_WRITE_KEY", "")


def _check_write_auth(headers: dict) -> tuple[int, dict] | None:
    """Return an error tuple if write auth fails, None if OK."""
    if not WRITE_KEY:
        return None
    auth = headers.get("authorization", "")
    if auth == f"Bearer {WRITE_KEY}":
        return None
    return 401, _error("unauthorized", "invalid or missing write key")


def dispatch(
    method: str,
    path: str,
    body: object,
    db,
    llm_fn,
    headers: dict | None = None,
    remote_addr: str | None = None,
    classification_mode: str = "heuristic",
    suggestion_mode: str = "heuristic",
) -> tuple[int, dict, dict | None]:
    """Route a request to a handler. Returns (status, payload, extra_headers).

    `extra_headers` is None in the common case and only carries things like
    `Retry-After` on a rate-limited response.
    """
    headers = headers or {}

    def _rate_limit(name: str) -> tuple[int, dict, dict] | None:
        """Return a 429 response if the client exceeded `name`, else None."""
        client = limiter.client_key(
            headers.get("x-forwarded-for", ""), remote_addr or ""
        )
        allowed, retry_after = limiter.hit(client, name)
        if not allowed:
            print(
                f"WARN: rate limit '{name}' hit for {client}; "
                f"blocking until ~{retry_after}s",
                file=sys.stderr,
                flush=True,
            )
            return (
                429,
                _error(
                    "rate_limited",
                    f"rate limit exceeded for {name}. "
                    f"Try again in about {retry_after}s.",
                ),
                {"Retry-After": str(retry_after)},
            )
        return None

    if method == "GET" and path == "/health":
        return 200, {"status": "ok"}, None
    if method == "GET" and path == "/scenarios":
        return 200, handle_scenarios(), None
    if method == "GET" and path == "/suggestions":
        blocked = _rate_limit("suggestions")
        if blocked:
            return blocked
        try:
            return (
                200,
                handle_suggestions(llm_fn, suggestion_mode=suggestion_mode),
                None,
            )
        except LLMError as exc:
            _log_remote_failure(
                "suggestions LLM",
                _request_context(method, path, body, suggestion_mode),
                exc,
            )
            return 502, _error("llm_failed", "language model request failed"), None
        except SuggestionResponseError as exc:
            _log_remote_failure(
                "suggestions response",
                _request_context(method, path, body, suggestion_mode),
                exc,
            )
            return (
                502,
                _error("suggestions_failed", "suggestion generation failed"),
                None,
            )
    if method == "POST" and path == "/ask":
        if not isinstance(body, dict):
            return (
                400,
                _error("invalid_request", "request body must be a JSON object"),
                None,
            )
        raw_question = body.get("question")
        if raw_question is not None and not isinstance(raw_question, str):
            return (
                400,
                _error("invalid_request", "'question' must be a string"),
                None,
            )
        question = (body.get("question") or "").strip()
        if not question:
            return (
                400,
                _error("invalid_request", "missing 'question' in request body"),
                None,
            )
        if len(question) > 300:
            return (
                400,
                _error("invalid_request", "question too long (max 300 chars)"),
                None,
            )
        blocked = _rate_limit("ask")
        if blocked:
            return blocked
        try:
            return 200, handle_ask(question, db, llm_fn, classification_mode), None
        except LLMError as exc:
            _log_remote_failure(
                "ask LLM",
                _request_context(method, path, body, classification_mode),
                exc,
            )
            return 502, _error("llm_failed", "language model request failed"), None
        except ClassificationError as exc:
            _log_remote_failure(
                "ask classifier",
                _request_context(method, path, body, classification_mode),
                exc,
            )
            return (
                502,
                _error("classifier_failed", "question classification failed"),
                None,
            )
        except HydraDBError as exc:
            _log_remote_failure(
                "ask graph backend",
                _request_context(method, path, body, classification_mode),
                exc,
            )
            return 502, _error("graph_backend_failed", "graph backend failed"), None
    if method == "GET" and path == "/graph":
        try:
            return 200, handle_graph(db), None
        except HydraDBError as exc:
            _log_remote_failure(
                "graph backend",
                _request_context(method, path, body, classification_mode),
                exc,
            )
            return 502, _error("graph_backend_failed", "graph backend failed"), None
    if method in ("POST",) and path in ("/ingest", "/ingest/slack"):
        auth_err = _check_write_auth(headers)
        if auth_err:
            return auth_err[0], auth_err[1], None
        blocked = _rate_limit("ingest")
        if blocked:
            return blocked
        if method == "POST" and path == "/ingest":
            if not isinstance(body, dict):
                return (
                    400,
                    _error("invalid_request", "request body must be a JSON object"),
                    None,
                )
            from hydraclaim.ingest_api import handle_ingest

            status, payload = handle_ingest(body, db)
            return status, payload, None
        from hydraclaim.ingest_api import handle_ingest_slack

        status, payload = handle_ingest_slack(body, db)
        return status, payload, None
    return 404, _error("not_found", f"unknown endpoint: {method} {path}"), None


class DemoHandler(BaseHTTPRequestHandler):
    """HTTP plumbing around dispatch()."""

    def _respond(self, status: int, payload: dict, extra: dict | None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self) -> tuple[int, dict, dict | None]:
        llm_fn = self.server.llm_fn  # type: ignore[attr-defined]
        classification_mode = getattr(self.server, "classification_mode", "heuristic")
        suggestion_mode = getattr(self.server, "suggestion_mode", "heuristic")
        try:
            if self.command == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length > 500_000:  # hard cap on request bodies
                    return (
                        413,
                        _error("request_too_large", "request body too large"),
                        None,
                    )
                body = json.loads(self.rfile.read(length) or b"{}")
            else:
                body = {}
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            hdrs = {k.lower(): v for k, v in self.headers.items()}
            remote_addr = self.client_address[0] if self.client_address else None
            return dispatch(
                self.command,
                path,
                body,
                self.server.db,
                llm_fn,
                hdrs,
                remote_addr,
                classification_mode,
                suggestion_mode,
            )  # type: ignore[attr-defined]
        except json.JSONDecodeError:
            return 400, _error("invalid_json", "request body must be JSON"), None

    def do_GET(self) -> None:
        self._respond(*self._dispatch())

    def do_POST(self) -> None:
        self._respond(*self._dispatch())

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # quiet: access logging goes to the service manager's journal


def main() -> None:
    parser = argparse.ArgumentParser(prog="hydraclaim.serve")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--llm",
        action="store_true",
        help="use the LLM for question classification "
        "(requires LLM_API_KEY); default is the keyword heuristic",
    )
    args = parser.parse_args()

    from hydraclaim.config import connect

    classification_mode = "llm" if args.llm else "heuristic"
    llm_fn = llm_classifier if args.llm else None
    db = connect()
    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    server.db = db  # type: ignore[attr-defined]
    server.llm_fn = llm_fn  # type: ignore[attr-defined]
    server.classification_mode = classification_mode  # type: ignore[attr-defined]
    server.suggestion_mode = classification_mode  # type: ignore[attr-defined]
    mode = f"{classification_mode} classification"
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
