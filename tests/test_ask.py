from __future__ import annotations

import sys

from hydraclaim import ask, config, retrieve


class _FakeConnection:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def test_llm_option_selects_llm_classification_mode(monkeypatch):
    seen = {}

    def fake_answer(db, question, **kwargs):
        seen.update(kwargs)
        return {
            "route": "ABSTAIN",
            "answer": "none",
            "citations": [],
            "classification": {},
            "probe": None,
        }

    monkeypatch.setattr(config, "connect", lambda: _FakeConnection())
    monkeypatch.setattr(retrieve, "answer", fake_answer)
    monkeypatch.setattr(
        sys,
        "argv",
        ["hydraclaim.ask", "Who owns launch?", "--llm"],
    )

    ask.main()

    assert seen["classification_mode"] == "llm"
    assert seen["llm_fn"] is not None
