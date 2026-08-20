"""Bounded, typed reads for claims and answers.

This module owns the read boundary for HydraClaim.  Every claim and relation
query is scoped to one subject and, when supplied, one predicate.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from hydraclaim.cypher import to_cypher_literal as lit
from hydraclaim.db import HydraDB
from hydraclaim.errors import GraphIntegrityError
from hydraclaim.model import split_aliases
from hydraclaim.scoring import rank_claims

if TYPE_CHECKING:
    from hydraclaim.router import Classification


DEFAULT_CLAIM_READ_LIMIT = 25
MAX_CHAIN_DEPTH = 5


@dataclass(frozen=True)
class ClaimScope:
    subject: str
    predicate: str | None = None
    active_only: bool = False
    as_of: str | None = None
    limit: int = DEFAULT_CLAIM_READ_LIMIT


class ClaimReadLimitError(ValueError):
    """Raised when a bounded claim scope contains more matches than its limit."""

    def __init__(
        self,
        message: str = "claim scope limit exceeded",
        *,
        subject: str | None = None,
        predicate: str | None = None,
        limit: int | None = None,
    ) -> None:
        super().__init__(message)
        self.subject = subject
        self.predicate = predicate
        self.limit = limit


@dataclass(frozen=True)
class ClaimView:
    id: int
    key: str | None
    subject: str
    predicate: str
    value: str
    valid_from: str
    valid_to: str | None
    status: str
    confidence: float | None
    quote: str | None
    explicitness: float | None
    extraction_confidence: float | None
    source_kind: str | None
    author: str | None


@dataclass(frozen=True)
class ProbeResult:
    subject: str
    predicate: str | None
    coverage: int
    conflicts: int
    distinct_active_values: int
    chain_depth: int


@dataclass(frozen=True)
class Citation:
    claim_id: str | int
    value: str
    valid_from: str
    valid_to: str | None
    source_kind: str | None
    author: str | None
    quote: str | None


@dataclass(frozen=True)
class AnswerResult:
    route: str
    text: str
    citations: tuple[Citation, ...]
    classification: Classification
    probe: ProbeResult | None

    def as_dict(self) -> dict:
        """Return the legacy mapping shape used by CLI and HTTP callers."""
        return {
            "route": self.route,
            "answer": self.text,
            "citations": [asdict(citation) for citation in self.citations],
            "classification": asdict(self.classification),
            "probe": asdict(self.probe) if self.probe is not None else None,
        }


def _chain_depth(edges: list[tuple[int, int]], ids: set[int]) -> int:
    """Return the longest selected supersession path in edges."""
    forward: dict[int, set[int]] = {}
    for new_id, old_id in edges:
        if new_id in ids and old_id in ids:
            forward.setdefault(new_id, set()).add(old_id)

    indegree = {node: 0 for node in ids}
    for children in forward.values():
        for child in children:
            indegree[child] = indegree.get(child, 0) + 1
    pending = [node for node, degree in indegree.items() if degree == 0]
    depth = {node: 0 for node in indegree}
    processed = 0
    while pending:
        node = pending.pop()
        processed += 1
        for child in sorted(forward.get(node, ())):
            depth[child] = max(depth[child], depth[node] + 1)
            indegree[child] -= 1
            if indegree[child] == 0:
                pending.append(child)
    if processed != len(indegree):
        raise GraphIntegrityError("supersession cycle detected in selected claims")
    return max(depth.values(), default=0)


def _claim_dict(claim: ClaimView) -> dict:
    return asdict(claim)


class ClaimReader:
    """Read claims, probes, relations, and deterministic answers."""

    def __init__(self, db: HydraDB) -> None:
        self._db = db

    def read_entities(self) -> tuple[dict, ...]:
        rows = self._db.query(
            "MATCH (e:Entity) RETURN e.id AS id, e.name AS name, "
            "e.type AS type, e.aliases AS aliases "
            "ORDER BY e.name ASC, e.id ASC"
        )
        rows = sorted(rows, key=lambda row: (str(row["name"]), int(row.get("id", 0))))
        return tuple(
            {
                "id": row.get("id"),
                "name": row["name"],
                "type": row.get("type"),
                "aliases": split_aliases(row.get("aliases")),
            }
            for row in rows
        )

    def read_claims(self, scope: ClaimScope) -> tuple[ClaimView, ...]:
        if (
            not isinstance(scope.subject, str)
            or not scope.subject.strip()
            or not isinstance(scope.limit, int)
            or isinstance(scope.limit, bool)
            or scope.limit < 1
        ):
            raise ValueError("claim scope requires a subject and positive limit")

        clauses = [f"e.name = {lit(scope.subject)}"]
        if scope.predicate:
            clauses.append(f"c.predicate = {lit(scope.predicate)}")
        if scope.active_only:
            clauses.append("c.status = 'active'")
        if scope.as_of:
            clauses.append(
                f"(c.recorded_at <= {lit(scope.as_of)} "
                f"AND (c.valid_to = '' OR c.valid_to > {lit(scope.as_of)}))"
            )
        rows = self._db.query(
            f"""
MATCH (c:Claim)-[:ABOUT]->(e:Entity)
WHERE {" AND ".join(clauses)}
OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(ev:Evidence)-[:FROM]->(s:Source)
RETURN c.id AS id, c.key AS key, e.name AS subject, c.predicate AS predicate,
       c.value AS value, c.valid_from AS valid_from, c.valid_to AS valid_to,
       c.status AS status, c.confidence AS confidence,
       ev.quote AS quote, ev.explicitness AS explicitness,
       ev.extraction_confidence AS extraction_confidence,
       s.kind AS source_kind, s.author AS author
ORDER BY c.valid_from DESC, c.id DESC
LIMIT {int(scope.limit) + 1}"""
        )
        rows = sorted(
            rows,
            key=lambda row: (
                str(row["valid_from"]),
                int(row.get("id", 0)),
            ),
            reverse=True,
        )
        if len(rows) > scope.limit:
            predicate = scope.predicate or "*"
            raise ClaimReadLimitError(
                "claim scope limit exceeded for "
                f"subject={scope.subject!r}, predicate={predicate!r}, "
                f"limit={scope.limit}; more matches exist",
                subject=scope.subject,
                predicate=scope.predicate,
                limit=scope.limit,
            )
        return tuple(
            self._to_claim_view(row, scope, index) for index, row in enumerate(rows, 1)
        )

    @staticmethod
    def _to_claim_view(row: dict, scope: ClaimScope, ordinal: int) -> ClaimView:
        return ClaimView(
            id=int(row.get("id", ordinal)),
            key=row.get("key"),
            subject=row.get("subject", scope.subject),
            predicate=row["predicate"],
            value=row["value"],
            valid_from=row["valid_from"],
            valid_to=row.get("valid_to", ""),
            status=row["status"],
            confidence=row.get("confidence"),
            quote=row.get("quote"),
            explicitness=row.get("explicitness"),
            extraction_confidence=row.get("extraction_confidence"),
            source_kind=row.get("source_kind"),
            author=row.get("author"),
        )

    def read_relations(
        self, scope: ClaimScope, claim_ids: set[int], relation_type: str
    ) -> tuple[dict, ...]:
        if relation_type not in {"SUPERSEDES", "CONTRADICTS"}:
            raise ValueError(f"unsupported claim relation: {relation_type!r}")
        if not claim_ids:
            return ()
        if relation_type == "SUPERSEDES":
            return_fields = "a.id AS new_id, b.id AS old_id"
            relation_match = "-[:SUPERSEDES]->"
        else:
            return_fields = "a.id AS a_id, b.id AS b_id, r.resolved AS resolved"
            relation_match = "-[r:CONTRADICTS]->"
        rows: list[dict] = []
        selected_ids = ", ".join(str(int(claim_id)) for claim_id in sorted(claim_ids))
        for claim_id in sorted(claim_ids):
            if self._read_claim_scope_membership(claim_id, scope) is None:
                continue
            predicates = [f"b.id IN [{selected_ids}]"]
            if scope.predicate:
                predicates.extend(
                    [
                        f"a.predicate = {lit(scope.predicate)}",
                        f"b.predicate = {lit(scope.predicate)}",
                    ]
                )
            predicate_clause = "WHERE " + " AND ".join(predicates)
            relation_rows = self._db.query(
                f"""
MATCH (a:Claim {{id: {int(claim_id)}}}){relation_match}(b:Claim)
{predicate_clause}
RETURN {return_fields}
ORDER BY a.id ASC, b.id ASC
LIMIT {int(scope.limit) + 1}"""
            )
            if len(relation_rows) > scope.limit:
                raise ClaimReadLimitError(
                    "claim relation limit exceeded for "
                    f"subject={scope.subject!r}, predicate={(scope.predicate or '*')!r}, "
                    f"limit={scope.limit}; more relations exist",
                    subject=scope.subject,
                    predicate=scope.predicate,
                    limit=scope.limit,
                )
            for row in relation_rows:
                target_key = "old_id" if relation_type == "SUPERSEDES" else "b_id"
                target_id = int(row.get(target_key, row.get("dst")))
                if target_id not in claim_ids:
                    continue
                if self._read_claim_scope_membership(target_id, scope) is None:
                    continue
                rows.append(row)
                if len(rows) > scope.limit:
                    raise ClaimReadLimitError(
                        "claim relation limit exceeded for "
                        f"subject={scope.subject!r}, predicate={(scope.predicate or '*')!r}, "
                        f"limit={scope.limit}; more relations exist",
                        subject=scope.subject,
                        predicate=scope.predicate,
                        limit=scope.limit,
                    )
        if relation_type == "SUPERSEDES":
            rows = [
                {
                    "new_id": row.get("new_id", row.get("src")),
                    "old_id": row.get("old_id", row.get("dst")),
                }
                for row in rows
            ]
            rows = [
                row
                for row in rows
                if int(row["new_id"]) in claim_ids and int(row["old_id"]) in claim_ids
            ]
            rows.sort(key=lambda row: (int(row["new_id"]), int(row["old_id"])))
        else:
            rows = [
                {
                    "a_id": row.get("a_id", row.get("src")),
                    "b_id": row.get("b_id", row.get("dst")),
                    "resolved": row.get("resolved", False),
                }
                for row in rows
            ]
            rows = [
                row
                for row in rows
                if int(row["a_id"]) in claim_ids and int(row["b_id"]) in claim_ids
            ]
            rows.sort(key=lambda row: (int(row["a_id"]), int(row["b_id"])))
        return tuple(rows)

    def read_chain(self, claim_id: int, scope: ClaimScope) -> tuple[dict, ...]:
        start = self._read_claim_scope_membership(claim_id, scope)
        if start is None:
            return ()
        start_predicate = start["predicate"]
        predicate_clauses = ["current.predicate = older.predicate"]
        if scope.predicate:
            predicate_clauses.extend(
                [
                    f"current.predicate = {lit(scope.predicate)}",
                    f"older.predicate = {lit(scope.predicate)}",
                ]
            )
        if scope.as_of:
            predicate_clauses.extend(
                [
                    f"older.recorded_at <= {lit(scope.as_of)}",
                    f"(older.valid_to = '' OR older.valid_to > {lit(scope.as_of)})",
                ]
            )
        scoped_rows = []
        pending = [(int(claim_id), 0, frozenset({int(claim_id)}))]
        seen_ids = {int(claim_id)}
        scheduled_ids = {int(claim_id)}
        expanded_ids: set[int] = set()
        cursor = 0
        while cursor < len(pending):
            current_id, current_depth, path = pending[cursor]
            cursor += 1
            if current_id in expanded_ids:
                continue
            expanded_ids.add(current_id)
            if current_depth >= MAX_CHAIN_DEPTH:
                continue
            rows = self._db.query(
                f"""
MATCH (current:Claim {{id: {current_id}}})-[:SUPERSEDES]->(older:Claim)
WHERE {" AND ".join(predicate_clauses)}
OPTIONAL MATCH (older)-[:ABOUT]->(older_entity:Entity)
OPTIONAL MATCH (older)-[:SUPPORTED_BY]->(older_ev:Evidence)-[:FROM]->(older_source:Source)
RETURN older.id AS id, older.value AS value,
       older.key AS key, older.valid_from AS valid_from, older.valid_to AS valid_to,
       older_entity.name AS subject, older.predicate AS predicate,
       older_ev.quote AS quote, older_source.kind AS source_kind,
       older_source.author AS author
ORDER BY older.valid_from DESC, older.id DESC
LIMIT {int(scope.limit) + 1}"""
            )
            if len(rows) > scope.limit:
                raise ClaimReadLimitError(
                    "claim chain relation limit exceeded for "
                    f"subject={scope.subject!r}, predicate={(scope.predicate or '*')!r}, "
                    f"limit={scope.limit}; more relations exist",
                    subject=scope.subject,
                    predicate=scope.predicate,
                    limit=scope.limit,
                )
            rows = sorted(
                rows,
                key=lambda row: (str(row["valid_from"]), int(row.get("id", 0))),
                reverse=True,
            )
            for row in rows:
                older_id = int(row["id"])
                if older_id in path:
                    raise GraphIntegrityError(
                        "supersession cycle detected while reading claim chain"
                    )
                if row["predicate"] != start_predicate:
                    continue
                older = self._read_claim_scope_membership(older_id, scope)
                if older is None or older["predicate"] != start_predicate:
                    continue
                if older_id not in seen_ids:
                    scoped_rows.append(
                        {
                            **row,
                            "subject": older["subject"],
                            "predicate": older["predicate"],
                        }
                    )
                    seen_ids.add(older_id)
                    if len(scoped_rows) > scope.limit:
                        raise ClaimReadLimitError(
                            "claim chain limit exceeded for "
                            f"subject={scope.subject!r}, predicate={(scope.predicate or '*')!r}, "
                            f"limit={scope.limit}; more claims exist",
                            subject=scope.subject,
                            predicate=scope.predicate,
                            limit=scope.limit,
                        )
                if older_id not in scheduled_ids:
                    pending.append(
                        (older_id, current_depth + 1, path | {older_id})
                    )
                    scheduled_ids.add(older_id)
        return tuple(
            sorted(
                scoped_rows,
                key=lambda row: (str(row["valid_from"]), int(row.get("id", 0))),
                reverse=True,
            )
        )

    def _read_claim_scope_membership(
        self, claim_id: int, scope: ClaimScope
    ) -> dict | None:
        clauses = [f"e.name = {lit(scope.subject)}"]
        if scope.predicate:
            clauses.append(f"c.predicate = {lit(scope.predicate)}")
        if scope.as_of:
            clauses.extend(
                [
                    f"c.recorded_at <= {lit(scope.as_of)}",
                    f"(c.valid_to = '' OR c.valid_to > {lit(scope.as_of)})",
                ]
            )
        rows = self._db.query(
            f"""
MATCH (c:Claim {{id: {int(claim_id)}}})-[:ABOUT]->(e:Entity {{name: {lit(scope.subject)}}})
WHERE {" AND ".join(clauses)}
RETURN c.id AS id, e.name AS subject, c.predicate AS predicate
LIMIT {int(scope.limit) + 1}"""
        )
        if len(rows) > scope.limit:
            raise ClaimReadLimitError(
                "claim scope relation limit exceeded",
                subject=scope.subject,
                predicate=scope.predicate,
                limit=scope.limit,
            )
        for row in rows:
            if int(row["id"]) != int(claim_id):
                continue
            if row.get("subject") != scope.subject:
                continue
            if scope.predicate and row.get("predicate") != scope.predicate:
                continue
            return row
        return None

    def probe(self, scope: ClaimScope) -> ProbeResult:
        claims = self.read_claims(
            ClaimScope(
                subject=scope.subject,
                predicate=scope.predicate,
                as_of=scope.as_of,
                limit=scope.limit,
            )
        )
        coverage = len(claims)
        active_ids = {claim.id for claim in claims if claim.status == "active"}
        distinct_values = len(
            {claim.value.strip().lower() for claim in claims if claim.id in active_ids}
        )
        conflicts = 0
        chain_depth = 0
        if scope.predicate and coverage:
            claim_ids = {claim.id for claim in claims}
            supersedes = self.read_relations(scope, claim_ids, "SUPERSEDES")
            sup_edges = [(int(row["new_id"]), int(row["old_id"])) for row in supersedes]
            chain_depth = _chain_depth(sup_edges, claim_ids)
            contradictions = self.read_relations(scope, claim_ids, "CONTRADICTS")
            conflicts = len(
                {
                    tuple(sorted((int(row["a_id"]), int(row["b_id"]))))
                    for row in contradictions
                    if not row.get("resolved", False)
                    and (
                        int(row["a_id"]) in active_ids or int(row["b_id"]) in active_ids
                    )
                }
            )
        return ProbeResult(
            subject=scope.subject,
            predicate=scope.predicate,
            coverage=coverage,
            conflicts=conflicts,
            distinct_active_values=distinct_values,
            chain_depth=chain_depth,
        )

    def answer(
        self,
        question: str,
        *,
        classification_mode: str = "heuristic",
        llm_fn=None,
        now: datetime | None = None,
        force_route: str | None = None,
    ) -> AnswerResult:
        from hydraclaim.router import (
            ROUTE_ABSTAIN,
            ROUTE_FAST,
            classify,
            decide_route,
        )

        now = now or datetime.now(timezone.utc)
        roster = list(self.read_entities())
        classification: Classification = classify(
            question,
            roster,
            mode=classification_mode,
            llm_fn=llm_fn,
            now=now,
        )
        if classification.subject is None:
            return AnswerResult(
                route=ROUTE_ABSTAIN,
                text=abstain_message(question, None),
                citations=(),
                classification=classification,
                probe=None,
            )

        scope = ClaimScope(
            subject=classification.subject,
            predicate=classification.predicate,
            as_of=classification.as_of,
        )
        probe_result = self.probe(scope)
        route = force_route or decide_route(classification.question_type, probe_result)
        if route == ROUTE_ABSTAIN:
            if classification.predicate is None and probe_result.coverage:
                active_any = self.read_claims(
                    ClaimScope(
                        subject=classification.subject,
                        active_only=True,
                        as_of=classification.as_of,
                    )
                )
                available = sorted({claim.predicate for claim in active_any})
                text = abstain_uncovered_message(classification.subject, available)
            else:
                text = abstain_message(classification.subject, classification.predicate)
            return AnswerResult(route, text, (), classification, probe_result)

        active = self.read_claims(
            ClaimScope(
                subject=classification.subject,
                predicate=classification.predicate,
                active_only=True,
                as_of=classification.as_of,
            )
        )
        if not active:
            text = (
                abstain_message(classification.subject, classification.predicate)
                + " (Claims exist in history, but none are currently active"
                + (f" as of {classification.as_of}." if classification.as_of else ".")
            )
            return AnswerResult(ROUTE_ABSTAIN, text, (), classification, probe_result)

        if classification.predicate and _ORIGIN_RE.search(question):
            history = self.read_claims(scope)
            oldest = min(
                history,
                key=lambda claim: (claim.valid_from, str(claim.key or claim.id)),
            )
            return AnswerResult(
                route,
                build_fast_answer(_claim_dict(oldest)),
                (_citation(oldest),),
                classification,
                probe_result,
            )

        if classification.question_type == "temporal" and classification.predicate:
            history = self.read_claims(scope)
            if (
                "before the most recent change" in question.lower()
                and len(history) >= 2
            ):
                current, previous = history[0], history[1]
                return AnswerResult(
                    route,
                    build_temporal_answer(_claim_dict(current), _claim_dict(previous)),
                    (_citation(previous),),
                    classification,
                    probe_result,
                )

        if route == ROUTE_FAST:
            return AnswerResult(
                route,
                build_fast_answer(_claim_dict(active[0])),
                (_citation(active[0]),),
                classification,
                probe_result,
            )

        if probe_result.conflicts > 0 or probe_result.distinct_active_values > 1:
            ranked = rank_claims(
                [_claim_dict(claim) for claim in active],
                classification.predicate or "",
                now,
            )
            ranked.sort(
                key=lambda pair: (
                    pair[1],
                    str(pair[0]["valid_from"]),
                    str(pair[0].get("key") or ""),
                    int(pair[0].get("id", 0)),
                ),
                reverse=True,
            )
            return AnswerResult(
                route,
                build_conflict_answer(
                    classification.subject, classification.predicate or "", ranked
                ),
                tuple(_citation(claim) for claim, _ in ranked),
                classification,
                probe_result,
            )

        chain = self.read_chain(active[0].id, scope)
        if chain:
            return AnswerResult(
                route,
                build_chain_answer(_claim_dict(active[0]), list(chain)),
                tuple(
                    [_citation(active[0])] + [_citation(ancestor) for ancestor in chain]
                ),
                classification,
                probe_result,
            )
        return AnswerResult(
            route,
            build_fast_answer(_claim_dict(active[0])),
            (_citation(active[0]),),
            classification,
            probe_result,
        )


def _citation(claim: ClaimView | dict) -> Citation:
    if isinstance(claim, ClaimView):
        claim_id = claim.key or claim.id
        value = claim.value
        valid_from = claim.valid_from
        valid_to = claim.valid_to
        source_kind = claim.source_kind
        author = claim.author
        quote = claim.quote
    else:
        claim_id = claim.get("key") or claim["id"]
        value = claim["value"]
        valid_from = claim["valid_from"]
        valid_to = claim.get("valid_to")
        source_kind = claim.get("source_kind")
        author = claim.get("author")
        quote = claim.get("quote")
    return Citation(
        claim_id=claim_id,
        value=value,
        valid_from=valid_from,
        valid_to=valid_to,
        source_kind=source_kind,
        author=author,
        quote=quote,
    )


def abstain_message(subject: str, predicate: str | None) -> str:
    if predicate:
        return (
            f"I don't have any recorded information about the {predicate} of "
            f"'{subject}'. I searched claims about '{subject}' with predicate "
            f"'{predicate}' and found none — the answer is not in the history."
        )
    return (
        f"I don't have any recorded information about '{subject}'. I searched "
        f"all claims about that entity and found none."
    )


def abstain_uncovered_message(subject: str, available: list[str]) -> str:
    if not available:
        return abstain_message(subject, None)
    tracked = ", ".join(available)
    return (
        f"I don't have a recorded fact that answers that about '{subject}'. "
        f"The claims I track for '{subject}' cover: {tracked} — none of those "
        f"match the question, so the answer is not in the history."
    )


_ORIGIN_RE = re.compile(r"\b(first|originally|initially|earliest)\b", re.IGNORECASE)


def build_fast_answer(claim: dict) -> str:
    return (
        f"{claim['subject']} — {claim['predicate']}: {claim['value']} "
        f"(as of {claim['valid_from']}, per {claim.get('source_kind')}/"
        f'{claim.get("author")}: "{claim.get("quote")}")'
    )


def build_chain_answer(head: dict, chain: list[dict]) -> str:
    lines = [
        f"{head['subject']} — {head['predicate']}: {head['value']} "
        f"(current, since {head['valid_from']}).",
        "Previously:",
    ]
    for ancestor in chain:
        lines.append(
            f"  - {ancestor['value']} ({ancestor['valid_from']} -> "
            f"{ancestor['valid_to'] or '?'})"
        )
    return "\n".join(lines)


def build_temporal_answer(current: dict, previous: dict) -> str:
    return (
        f"Before the most recent change on {current['valid_from']}, "
        f"{current['subject']} — {current['predicate']} was {previous['value']} "
        f"(from {previous['valid_from']} to "
        f"{previous['valid_to'] or current['valid_from']})."
    )


def build_conflict_answer(
    subject: str, predicate: str, ranked: list[tuple[dict, float]]
) -> str:
    lines = [f"Unresolved conflict about {subject} — {predicate}:"]
    for claim, score in ranked:
        lines.append(
            f"  - {claim['value']} — {claim.get('source_kind')}/{claim.get('author')}, "
            f'{claim["valid_from"]} (trust {score:.2f}): "{claim.get("quote")}"'
        )
    winner = ranked[0][0]
    lines.append(
        f"The highest-trust record says {winner['value']}, but the conflicting "
        f"records were never reconciled."
    )
    return "\n".join(lines)
