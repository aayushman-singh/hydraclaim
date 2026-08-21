"""Model Context Protocol server for HydraClaim."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from typing import Any

from mcp.server import MCPServer


def create_server(
    *,
    connect_fn: Callable | None = None,
    answer_fn: Callable | None = None,
    store_factory: Callable | None = None,
) -> MCPServer:
    """Create the HydraClaim MCP server over the existing graph interfaces."""
    if connect_fn is None:
        from hydraclaim.config import connect

        connect_fn = connect
    if answer_fn is None:
        from hydraclaim.retrieve import answer

        answer_fn = answer
    if store_factory is None:
        from hydraclaim.source_events import SourceEventStore

        store_factory = SourceEventStore

    server = MCPServer(
        "HydraClaim",
        description="Conflict-aware temporal memory backed by HydraDB.",
        instructions=(
            "Use ask_claim to get supported answers with citations. "
            "Use record_source_event only when the user asks you to save a source."
        ),
    )

    @server.tool()
    def ask_claim(question: str) -> dict[str, Any]:
        """Answer one question from the claim graph with evidence and route data."""
        if not question.strip():
            raise ValueError("question must be a non-empty string")
        with connect_fn() as db:
            return answer_fn(db, question, classification_mode="heuristic")

    @server.tool()
    def record_source_event(
        source_kind: str,
        author: str,
        occurred_at: str,
        content: str,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        """Save one exact source event before later claim extraction."""
        event = {
            "source_kind": source_kind,
            "author": author,
            "occurred_at": occurred_at,
            "content": content,
        }
        if source_id is not None:
            event["source_id"] = source_id
        with connect_fn() as db:
            return store_factory(db).capture(event)

    return server


def main(argv: Sequence[str] | None = None) -> int:
    """Run the HydraClaim MCP server through standard input and output."""
    from hydraclaim.config import command_epilog, require_settings

    parser = argparse.ArgumentParser(
        prog="hydraclaim mcp",
        description="Run the HydraClaim Model Context Protocol server over stdio.",
        epilog=command_epilog(hydradb=True),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.parse_args(argv)
    require_settings(hydradb=True)
    create_server().run(transport="stdio")
    return 0


if __name__ == "__main__":
    from hydraclaim.cli import run_module

    raise SystemExit(run_module("mcp", main))
