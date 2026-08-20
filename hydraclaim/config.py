"""Runtime configuration and command setting checks."""

from __future__ import annotations

import os

HYDRADB_URL = os.environ.get("HYDRADB_URL", "http://127.0.0.1:8443")
HYDRADB_TOKEN = os.environ.get("HYDRADB_TOKEN", "local-development-token-32-bytes")
HYDRADB_NAMESPACE = os.environ.get("HYDRADB_NAMESPACE", "default")
HYDRADB_GRAPH = os.environ.get("HYDRADB_GRAPH", "default")
HYDRADB_CELL = os.environ.get("HYDRADB_CELL", "cell-0")


class ConfigurationError(ValueError):
    """Raised when a command setting is missing or empty."""


HYDRADB_SETTING_NAMES = (
    "HYDRADB_URL",
    "HYDRADB_TOKEN",
    "HYDRADB_NAMESPACE",
    "HYDRADB_GRAPH",
    "HYDRADB_CELL",
)
LLM_SETTING_NAMES = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")


def _hydradb_settings() -> dict[str, str]:
    defaults = {
        "HYDRADB_URL": "http://127.0.0.1:8443",
        "HYDRADB_TOKEN": "local-development-token-32-bytes",
        "HYDRADB_NAMESPACE": "default",
        "HYDRADB_GRAPH": "default",
        "HYDRADB_CELL": "cell-0",
    }
    return {name: os.environ.get(name, default) for name, default in defaults.items()}


def require_settings(*, hydradb: bool = False, llm: bool = False) -> None:
    """Raise before external access when a required setting is empty."""
    missing: list[str] = []
    if hydradb:
        missing.extend(
            name for name, value in _hydradb_settings().items() if not value.strip()
        )
    if llm and not os.environ.get("LLM_API_KEY", "").strip():
        missing.append("LLM_API_KEY")
    if missing:
        raise ConfigurationError(
            "missing required settings: " + ", ".join(dict.fromkeys(missing))
        )


def command_epilog(*, hydradb: bool = False, llm: str | None = None) -> str:
    """Return short setting guidance for a command help screen."""
    lines = []
    if hydradb:
        lines.append(
            "HydraDB settings: HYDRADB_URL, HYDRADB_TOKEN, "
            "HYDRADB_NAMESPACE, HYDRADB_GRAPH, HYDRADB_CELL."
        )
    if llm == "required":
        lines.append(
            "LLM settings: LLM_API_KEY is required; "
            "LLM_BASE_URL and LLM_MODEL are optional."
        )
    elif llm == "optional":
        lines.append(
            "LLM settings: --llm requires LLM_API_KEY; "
            "LLM_BASE_URL and LLM_MODEL are optional. "
            "LLM_API_KEY alone does not select LLM mode."
        )
    return "\n".join(lines)


def connect():
    """Open a HydraDB client from the current environment."""
    from hydraclaim.db import HydraDB  # lazy: keeps httpx out of offline paths

    require_settings(hydradb=True)
    settings = _hydradb_settings()
    return HydraDB(
        base_url=settings["HYDRADB_URL"],
        token=settings["HYDRADB_TOKEN"],
        namespace=settings["HYDRADB_NAMESPACE"],
        graph_id=settings["HYDRADB_GRAPH"],
        cell_id=settings["HYDRADB_CELL"],
    )
