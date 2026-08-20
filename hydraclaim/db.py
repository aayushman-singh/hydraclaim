"""Thin HydraDB client over the HTTP JSON query API.

HydraDB also speaks Bolt 5.x (Neo4j drivers) — the HTTP API keeps this
project's dependency footprint to httpx only and matches the examples in
the HydraDB README. Responses arrive as `{"columns": [...], "rows": [[...]]}`
with cells as typed envelopes such as `{"type": "vertex_id", "value": 2}`;
`query` zips columns with rows and `unwrap` normalizes cells to plain values.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class HydraDBError(RuntimeError):
    pass


def unwrap(value: Any) -> Any:
    """Collapse HydraDB typed value envelopes into plain Python values."""
    if isinstance(value, dict):
        if "type" in value and "value" in value and len(value) <= 3:
            return unwrap(value["value"])
        return {k: unwrap(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unwrap(v) for v in value]
    return value


class HydraDB:
    def __init__(
        self,
        base_url: str,
        token: str,
        namespace: str = "default",
        graph_id: str = "default",
        cell_id: str = "cell-0",
        timeout: float = 30.0,
    ) -> None:
        self._graph_id = graph_id
        self._cell_id = cell_id
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Graph-Namespace": namespace,
                "Content-Type": "application/json",
            },
        )

    def query(self, cypher: str, consistency: str = "causal") -> list[dict[str, Any]]:
        endpoint = f"/v1/graphs/{self._graph_id}/query"
        context = (
            f"endpoint={endpoint} graph_id={self._graph_id!r} "
            f"consistency={consistency!r} query_length={len(cypher)}"
        )
        try:
            resp = self._client.post(
                endpoint,
                json={
                    "cell_id": self._cell_id,
                    "query": cypher,
                    "consistency": consistency,
                },
            )
        except httpx.HTTPError as exc:
            raise HydraDBError(f"HydraDB transport failure ({context})") from exc
        if resp.status_code != 200:
            raise HydraDBError(
                f"HydraDB query failed HTTP {resp.status_code} ({context})"
            )
        try:
            payload = resp.json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise HydraDBError(f"HydraDB invalid JSON response ({context})") from exc

        if isinstance(payload, dict) and "columns" in payload and "rows" in payload:
            columns = payload["columns"]
            rows = payload["rows"]
            if not isinstance(columns, list) or any(
                not isinstance(column, str) for column in columns
            ):
                raise HydraDBError(f"HydraDB malformed response ({context})")
            if not isinstance(rows, list) or any(
                not isinstance(row, list) or len(row) != len(columns) for row in rows
            ):
                raise HydraDBError(f"HydraDB malformed response ({context})")
            return [
                {column: unwrap(value) for column, value in zip(columns, row)}
                for row in rows
            ]

        rows = payload.get("rows") if isinstance(payload, dict) else payload
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise HydraDBError(f"HydraDB malformed response ({context})")
        return [{key: unwrap(value) for key, value in row.items()} for row in rows]

    def query_one(
        self, cypher: str, consistency: str = "causal"
    ) -> dict[str, Any] | None:
        rows = self.query(cypher, consistency=consistency)
        return rows[0] if rows else None

    def node_exists(self, label: str, node_id: str) -> bool:
        from hydraclaim.cypher import to_cypher_literal

        row = self.query_one(
            f"MATCH (n:{label} {{id: {to_cypher_literal(node_id)}}}) RETURN n.id AS id LIMIT 1"
        )
        return row is not None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HydraDB":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
