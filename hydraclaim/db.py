"""Thin HydraDB client over the HTTP JSON query API.

HydraDB also speaks Bolt 5.x (Neo4j drivers) — the HTTP API keeps this
project's dependency footprint to httpx only and matches the examples in
the HydraDB README. Responses arrive as `{"columns": [...], "rows": [[...]]}`
with cells as typed envelopes such as `{"type": "vertex_id", "value": 2}`;
`query` zips columns with rows and `unwrap` normalizes cells to plain values.
"""

from __future__ import annotations

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
        resp = self._client.post(
            f"/v1/graphs/{self._graph_id}/query",
            json={"cell_id": self._cell_id, "query": cypher, "consistency": consistency},
        )
        if resp.status_code != 200:
            raise HydraDBError(
                f"query failed (HTTP {resp.status_code}): {resp.text[:500]}\n"
                f"cypher: {cypher[:300]}"
            )
        payload = resp.json()
        if isinstance(payload, dict) and "columns" in payload and "rows" in payload:
            # Query API shape: rows are positional arrays aligned with columns.
            columns = payload["columns"]
            return [
                {col: unwrap(val) for col, val in zip(columns, row)}
                for row in payload["rows"]
            ]
        rows = payload.get("rows", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise HydraDBError(f"unexpected response shape: {str(payload)[:500]}")
        return [
            {k: unwrap(v) for k, v in row.items()} if isinstance(row, dict) else unwrap(row)
            for row in rows
        ]

    def query_one(self, cypher: str, consistency: str = "causal") -> dict[str, Any] | None:
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
