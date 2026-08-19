"""Runtime configuration (environment-driven, with local-dev defaults)."""

from __future__ import annotations

import os

HYDRADB_URL = os.environ.get("HYDRADB_URL", "http://127.0.0.1:8443")
HYDRADB_TOKEN = os.environ.get("HYDRADB_TOKEN", "local-development-token-32-bytes")
HYDRADB_NAMESPACE = os.environ.get("HYDRADB_NAMESPACE", "default")
HYDRADB_GRAPH = os.environ.get("HYDRADB_GRAPH", "default")
HYDRADB_CELL = os.environ.get("HYDRADB_CELL", "cell-0")


def connect():
    """Open a HydraDB client from the current environment."""
    from hydraclaim.db import HydraDB  # lazy: keeps httpx out of offline paths

    return HydraDB(
        base_url=HYDRADB_URL,
        token=HYDRADB_TOKEN,
        namespace=HYDRADB_NAMESPACE,
        graph_id=HYDRADB_GRAPH,
        cell_id=HYDRADB_CELL,
    )
