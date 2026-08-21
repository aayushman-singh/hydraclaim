from __future__ import annotations

from contextlib import contextmanager

import anyio
from mcp import Client


class FakeStore:
    def __init__(self, db):
        self.db = db

    def capture(self, event):
        return {
            "event_key": f"source-event:{event['source_kind']}:{event['source_id']}",
            "status": "CAPTURED",
            "created": True,
        }


def test_mcp_server_exposes_ask_and_record_tools():
    from hydraclaim.mcp_server import create_server

    database = object()

    @contextmanager
    def connect():
        yield database

    def answer(db, question, **kwargs):
        assert db is database
        assert question == "What is the launch date?"
        return {
            "route": "FAST",
            "answer": "April 18, 2026",
            "citations": [{"claim_id": "claim:launch-date"}],
            "classification": {"subject": "launch", "predicate": "deadline"},
            "probe": {"coverage": 1, "conflicts": 0},
        }

    server = create_server(
        connect_fn=connect,
        answer_fn=answer,
        store_factory=FakeStore,
    )

    async def exercise_server():
        async with Client(server) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "ask_claim",
                "record_source_event",
            }

            asked = await client.call_tool(
                "ask_claim", {"question": "What is the launch date?"}
            )
            assert asked.is_error is False
            assert asked.structured_content["answer"] == "April 18, 2026"
            assert asked.structured_content["citations"] == [
                {"claim_id": "claim:launch-date"}
            ]

            recorded = await client.call_tool(
                "record_source_event",
                {
                    "source_kind": "slack",
                    "author": "Asha Rao",
                    "occurred_at": "2026-08-20T10:30:00+00:00",
                    "content": "Launch moves to Monday.",
                    "source_id": "message-42",
                },
            )
            assert recorded.is_error is False
            assert recorded.structured_content == {
                "event_key": "source-event:slack:message-42",
                "status": "CAPTURED",
                "created": True,
            }

    anyio.run(exercise_server)
