import pytest

from trustgraph.llm import LLMError, extract_json


def test_plain_object():
    assert extract_json('{"claims": []}') == {"claims": []}


def test_fenced_json():
    text = 'Here you go:\n```json\n{"claims": [{"predicate": "deadline"}]}\n```\nDone.'
    assert extract_json(text) == {"claims": [{"predicate": "deadline"}]}


def test_prose_wrapped_array():
    assert extract_json('Sure! [{"a": 1}] hope that helps') == [{"a": 1}]


def test_garbage_raises():
    with pytest.raises(LLMError):
        extract_json("no json here at all")


def test_truncated_json_raises():
    with pytest.raises(LLMError):
        extract_json('{"claims": [{"predicate": "deadline"')


def test_chat_requires_api_key(monkeypatch):
    from trustgraph import llm

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        llm.chat([{"role": "user", "content": "hi"}])
