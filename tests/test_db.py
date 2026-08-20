from __future__ import annotations

import json

import httpx
import pytest

from hydraclaim.db import HydraDB, HydraDBError


class _Client:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def post(self, *args, **kwargs):
        if self.error is not None:
            raise self.error
        return self.response

    def close(self):
        return None


class _Response:
    status_code = 200
    text = "response"

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _db(monkeypatch, client):
    monkeypatch.setattr("hydraclaim.db.httpx.Client", lambda **kwargs: client)
    return HydraDB("http://graph", "secret-token")


def test_hydradb_wraps_transport_errors_with_safe_context(monkeypatch):
    db = _db(monkeypatch, _Client(error=httpx.ConnectError("offline")))

    with pytest.raises(HydraDBError, match="transport failure.*endpoint") as error:
        db.query("MATCH (n) RETURN n")

    assert "secret-token" not in str(error.value)
    assert "query_length=" in str(error.value)


def test_hydradb_wraps_json_decode_errors(monkeypatch):
    response = _Response(json.JSONDecodeError("bad", "", 0))
    db = _db(monkeypatch, _Client(response=response))

    with pytest.raises(HydraDBError, match="invalid JSON response"):
        db.query("RETURN 1")


@pytest.mark.parametrize(
    "payload",
    [
        {"columns": "id", "rows": []},
        {"columns": ["id"], "rows": [[1, 2]]},
        {"rows": ["not-a-row"]},
        {"rows": {"id": 1}},
    ],
)
def test_hydradb_rejects_malformed_response_shapes(monkeypatch, payload):
    db = _db(monkeypatch, _Client(response=_Response(payload)))

    with pytest.raises(HydraDBError, match="malformed response"):
        db.query("RETURN 1")
